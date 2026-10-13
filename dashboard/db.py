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

import psycopg2
import psycopg2.extras


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
SEARCH_PATH_OPTIONS = "-c search_path=tata_road,public"


def get_conn():
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg2.connect(url, options=SEARCH_PATH_OPTIONS)
    kwargs = {
        "port": os.environ.get("PGPORT", DEFAULTS["port"]),
        "user": os.environ.get("PGUSER", DEFAULTS["user"]),
        "password": os.environ.get("PGPASSWORD"),
        "dbname": os.environ.get("PGDATABASE", DEFAULTS["dbname"]),
        "options": SEARCH_PATH_OPTIONS,
    }
    host = os.environ.get("PGHOST")
    if host:
        kwargs["host"] = host
    gssencmode = os.environ.get("PGGSSENCMODE")
    if gssencmode:
        kwargs["gssencmode"] = gssencmode
    return psycopg2.connect(**kwargs)


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
