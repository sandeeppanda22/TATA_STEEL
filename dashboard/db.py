"""Postgres connection for the Tata Steel dashboard. No ORM — plain
psycopg2 + SQL (matches the project convention: direct PostgreSQL access).

Connection is resolved in this order:
  0. A repo-root .env file (if present) seeds any PG*/DATABASE_URL vars that
     are not already set in the real environment — see _load_dotenv below.
     rebuild.sh writes the DB password there once, so the server never prompts.
  1. DATABASE_URL env var (used in deployment — Render injects it from Neon)
  2. PGHOST / PGPORT / PGUSER / PGPASSWORD / PGDATABASE env vars (local dev,
     same variables rebuild.sh uses)
  3. Hard-coded local defaults (port 5432, user postgres, db tata_steel) —
     deliberately no default *host*. PGHOST is left unset unless the caller
     sets it, same as rebuild.sh and for the same reason: omitting host
     makes libpq use the local Unix socket, which skips GSSAPI entirely.
     Defaulting it to "localhost" forces TCP, where libpq tries GSSAPI first
     and fails on this machine with:
     Cannot find KDC for realm "APPLECONNECT.APPLE.COM".
     Set PGHOST explicitly for a remote server; PGGSSENCMODE=disable keeps
     TCP working when you do.

Every connection also sets search_path to tata_road (see SEARCH_PATH_OPTIONS
below) - the schema files only SET search_path for their own psql session,
so the server has to set it itself or unqualified table names 500 with
"relation ... does not exist" depending on the connecting role's own default.
"""
import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool


def _load_dotenv():
    """Load the repo-root .env into os.environ for any keys not already set.

    No python-dotenv dependency - the file is simple KEY=value lines written by
    rebuild.sh. A real environment variable (or a Render-injected DATABASE_URL)
    always wins, since we only fill keys that are not already present. Missing
    .env is fine (e.g. deployment, or before the first rebuild)."""
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(here, os.pardir, ".env")
    try:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass


_load_dotenv()

DEFAULTS = {
    "port": "5432",
    "user": "postgres",
    "dbname": "tata_steel",
}


# Every table in db/00_schema.sql lives in this schema, and the whole
# codebase (db_routes.py, calculations.py, ...) queries unqualified table
# names (e.g. "FROM road", "FROM edit_log"). The schema files SET search_path
# for their own psql session only - that is never persisted to the role, so
# the server's own connections need it set explicitly here, or an unqualified
# query 500s with "relation ... does not exist" the moment nothing else
# happens to have set search_path on the connecting role.
#
# This used to be passed as a libpq startup option (options="-c
# search_path=..."), which works against a plain Postgres connection but
# fails against Neon's pooled (PgBouncer) endpoint: PgBouncer rejects
# search_path as a startup parameter outright ("unsupported startup
# parameter in options: search_path"). Issuing it as a normal SET statement
# right after connecting works everywhere - direct Postgres, Neon pooled,
# and Neon unpooled alike - since it's just another statement in the same
# session/transaction rather than something PgBouncer has to proxy at
# connection setup.
SEARCH_PATH_SQL = "SET search_path TO tata_road, public"


# ---------------------------------------------------------------------------
# CONNECTION POOL
# ---------------------------------------------------------------------------
# Why a pool: without one, every db.query/execute/execute_returning call opened
# a brand-new psycopg2 connection and threw it away. Against a local socket
# that is merely wasteful; against Neon (cloud) each call paid a full TCP + TLS
# handshake AND the SET search_path round-trip BEFORE the real query — so a
# single page load, which fans out into dozens of per-road queries, stacked up
# dozens of connection setups back to back. That was the dominant cause of the
# dashboard loading slowly. A ThreadedConnectionPool keeps a small set of live
# connections open and hands them out, so the handshake happens once per
# connection, not once per query. uvicorn serves requests on a thread pool, so
# the pool must be the *threaded* variant.
#
# The pool is created lazily (first use) rather than at import so that importing
# this module never requires the DB to be reachable, and so a credential loaded
# later (e.g. .env written by rebuild.sh mid-process) is still picked up.
_POOL = None
_POOL_LOCK = threading.Lock()

# Keep this modest. Neon's free tier and PgBouncer pooling both cap concurrent
# connections; a handful is plenty for a single-user operational dashboard and
# stays well under any provider ceiling. maxconn can be raised later if real
# concurrency grows.
_POOL_MINCONN = 1
_POOL_MAXCONN = 8


def _connect_kwargs():
    """Resolve connection arguments exactly as the old get_conn() did.

    Returns either ("dsn", <url>) when DATABASE_URL is set (deployment / Neon),
    or ("kwargs", {...}) built from the PG* vars with the same PGHOST and
    PGGSSENCMODE handling — PGHOST omitted unless set (Unix socket, skips the
    GSSAPI/KDC failure on this machine), PGGSSENCMODE passed through only when
    present (keeps TCP working for a remote server)."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return "dsn", url
    kwargs = {
        "port": os.environ.get("PGPORT", DEFAULTS["port"]),
        "user": os.environ.get("PGUSER", DEFAULTS["user"]),
        "password": os.environ.get("PGPASSWORD"),
        "dbname": os.environ.get("PGDATABASE", DEFAULTS["dbname"]),
    }
    host = os.environ.get("PGHOST")
    if host:
        kwargs["host"] = host
    gssencmode = os.environ.get("PGGSSENCMODE")
    if gssencmode:
        kwargs["gssencmode"] = gssencmode
    return "kwargs", kwargs


def _get_pool():
    """Lazily create (once) and return the process-wide connection pool."""
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                mode, conf = _connect_kwargs()
                if mode == "dsn":
                    _POOL = psycopg2.pool.ThreadedConnectionPool(
                        _POOL_MINCONN, _POOL_MAXCONN, dsn=conf
                    )
                else:
                    _POOL = psycopg2.pool.ThreadedConnectionPool(
                        _POOL_MINCONN, _POOL_MAXCONN, **conf
                    )
    return _POOL


@contextmanager
def get_conn():
    """Acquire a pooled connection, set its search_path, and return it to the
    pool when done.

    Drop-in for the old get_conn(): still used as `with get_conn() as conn:`.
    The difference is lifecycle — the old version opened a fresh connection the
    caller's `with` block would commit/rollback but never close (leaked to GC);
    this version borrows from the pool and ALWAYS hands the connection back via
    putconn() in `finally`, so it is reused rather than recreated. search_path
    is re-applied on every checkout because a pooled connection may have been
    used by an earlier request (and because PgBouncer can reset session state).
    Commit/rollback stays the caller's job, same as before."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(SEARCH_PATH_SQL)
        yield conn
    finally:
        pool.putconn(conn)


def query(sql, params=None):
    """SELECT helper -> list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]


def execute(sql, params=None):
    """INSERT/UPDATE/DELETE helper. Returns rowcount."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()
            return cur.rowcount


def execute_returning(sql, params=None):
    """INSERT/UPDATE ... RETURNING helper -> list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = [dict(r) for r in cur.fetchall()]
            conn.commit()
            return rows
