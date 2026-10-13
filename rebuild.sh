#!/usr/bin/env bash
# Full build/rebuild for the dashboard: stop any running dev server, apply the
# DB schema and seed, confirm the API deps, confirm the UI static assets.
# Does NOT start the server - the exact start command is printed at the end.
#
# This is the ONE build/rebuild script. It lives at the repo root; starting
# the server afterward is a plain command, not a separate wrapper script.
#
# Covers all three tiers in one script:
#   0. Stop any dev server already running on $PORT
#   1. API   - confirm/install Python deps (fastapi, uvicorn, psycopg2, openpyxl)
#   2. UI    - confirm the static assets are present (no build step - plain
#              HTML/JS/CSS, nothing to compile)
#   3-7. DB  - INCREMENTAL rebuild of the local tata_road schema: regenerate
#              the seed from the source Excel, apply the (idempotent) schema,
#              load the seed (ON CONFLICT DO NOTHING - adds only what's new),
#              verify. No ALTER statements, no DROP SCHEMA - nothing already
#              in the database is ever discarded or overwritten by this
#              script, including weekly actuals, master-data edits, and any
#              road added through the dashboard's "Add Road" form.
#
# Usage:
#   ./rebuild.sh                 # prompts ONCE for the DB password if needed.
#                                # Adds whatever is new in Source/*.xlsx (or
#                                # was added via the UI) since the last run.
#                                # Existing roads/activities/weekly data/edits
#                                # are left exactly as they are.
#   PGUSER=... PGDATABASE=... ./rebuild.sh
#   PORT=9000 ./rebuild.sh       # match the port your dev server used
#   FORCE=1 ./rebuild.sh         # proceed even if the DB pre-flight check fails
#
# CONNECTION: PGHOST is deliberately NOT defaulted. With no PGHOST, libpq uses
# the local Unix socket, which skips GSSAPI entirely. Defaulting it to
# "localhost" forces TCP, where libpq tries GSSAPI first and fails on this
# machine with: Cannot find KDC for realm "APPLECONNECT.APPLE.COM".
# Set PGHOST explicitly for a remote server; PGGSSENCMODE=disable is set below
# so TCP still works when you do. (dashboard/db.py follows the same rule.)
#
# PASSWORD: asked for once per run and exported, so the psql calls below do
# not each prompt. If PGPASSWORD or a ~/.pgpass entry already covers it,
# nothing is asked.
#
# WHAT GETS DESTROYED: nothing. 00_schema.sql uses CREATE SCHEMA/TABLE/INDEX
# IF NOT EXISTS (no DROP SCHEMA), and 01_seed.sql uses ON CONFLICT DO NOTHING,
# so a road/activity already present - including its weekly actuals and any
# master-data edits - is left untouched. Only genuinely new rows get added.
# A real schema CHANGE (new column, new constraint) still needs a deliberate
# edit to 00_schema.sql plus a user ruling, same as before - this script just
# no longer has to wipe the database to apply it.
set -euo pipefail
# This script lives at the repo root, so its own directory IS the repo root.
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

ENV_FILE=".env"
# Load local DB credentials from .env if present, so neither this rebuild nor
# the dashboard server has to prompt. Only known PG* keys are read; the value
# is taken verbatim after the first '=' so passwords with special characters
# survive. A real environment variable already set always wins over the file.
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key val; do
        case "$key" in
            PGHOST|PGPORT|PGUSER|PGDATABASE|PGPASSWORD|PGGSSENCMODE|DATABASE_URL)
                val="${val%\"}"; val="${val#\"}"
                if [ -z "$(eval "echo \${$key:-}")" ]; then
                    export "$key=$val"
                fi
                ;;
        esac
    done < "$ENV_FILE"
fi

: "${PGPORT:=5432}"
: "${PGUSER:=postgres}"
: "${PGDATABASE:=tata_steel}"
: "${PGGSSENCMODE:=disable}"
export PGPORT PGUSER PGDATABASE PGGSSENCMODE
[ -n "${PGHOST:-}" ] && export PGHOST

echo "0/7 Stopping any dev server already running on port ${PORT} ..."
# Matches our own launcher (uvicorn dashboard.main:app) specifically, so this
# never kills an unrelated process that happens to share the port.
if pkill -f "uvicorn dashboard.main:app" 2>/dev/null; then
    sleep 1
    echo "    stopped."
else
    echo "    nothing running."
fi

echo
echo "1/7 Checking API dependencies (fastapi, uvicorn, psycopg2, openpyxl) ..."
python3 -c "import fastapi, uvicorn, psycopg2, openpyxl" 2>/dev/null || \
    python3 -m pip install --break-system-packages fastapi uvicorn psycopg2-binary openpyxl
echo "    ok."

echo
echo "2/7 Checking UI static assets (no build step - plain HTML/JS/CSS) ..."
for f in dashboard/static/index.html dashboard/static/dashboard.js dashboard/static/style.css; do
    if [ ! -f "$f" ]; then
        echo "Missing required UI asset: $f" >&2
        exit 1
    fi
done
echo "    ok."

if [ -z "${PGPASSWORD:-}" ] && [ ! -f "$HOME/.pgpass" ]; then
    printf 'Password for %s@%s: ' "$PGUSER" "${PGHOST:-local socket}" >&2
    read -rs PGPASSWORD
    printf '\n' >&2
    export PGPASSWORD
    # Persist to .env (gitignored, chmod 600) so the dashboard server and all
    # future rebuilds read it automatically - no more prompting, no manual
    # editing. Delete .env to be asked again, or edit it to change the value.
    ( umask 077
      {
        echo "# Local DB credentials for the dashboard - gitignored, never committed."
        echo "# Written by rebuild.sh; dashboard/db.py loads this at startup."
        [ -n "${PGHOST:-}" ] && echo "PGHOST=${PGHOST}"
        echo "PGPORT=${PGPORT}"
        echo "PGUSER=${PGUSER}"
        echo "PGDATABASE=${PGDATABASE}"
        echo "PGPASSWORD=${PGPASSWORD}"
      } > "$ENV_FILE" )
    chmod 600 "$ENV_FILE"
    echo "Saved DB credentials to ./.env (gitignored). The server will read them automatically." >&2
fi

PSQL="psql -v ON_ERROR_STOP=1"
echo
echo "Target: $PGUSER@${PGHOST:-local socket}:$PGPORT/$PGDATABASE"

echo
echo "3/7 Rebuilding dataset from Source/ ..."
python3 scripts/build_data.py

echo
echo "4/7 Regenerating seed SQL from Source/ (in-memory, no dataset.json) ..."
(cd scripts && python3 gen_seed.py)

echo
echo "5/7 Pre-flight check on the generated seed ..."
# Checks the dataset and the generated SQL BEFORE anything touches the DB -
# BR-05 weight totals, BR-04 major classification, DDL constraints, literal
# safety. Loading a seed that fails here puts known-wrong numbers in front of
# people, so the rebuild stops.
if ! python3 scripts/verify_seed.py; then
    if [ "${FORCE:-0}" = "1" ]; then
        echo "!! Pre-flight FAILED - continuing because FORCE=1." >&2
        echo "!! The loaded data will contain the defects listed above." >&2
    else
        echo >&2
        echo "Pre-flight failed. Nothing was written to the database." >&2
        echo "Fix the defect above, or re-run with FORCE=1 to load it anyway." >&2
        exit 1
    fi
fi

echo
echo "6/7 Applying schema (idempotent - creates only what's missing, drops nothing) ..."
$PSQL -f db/00_schema.sql

echo
echo "7/7 Loading seed (incremental - ON CONFLICT DO NOTHING, adds only new roads/activities/issues) ..."
$PSQL -f db/01_seed.sql

echo
echo "Verifying ..."
# Not ON_ERROR_STOP: 02_verify.sql is read-only and its job is to report, so
# a finding should print rather than abort the run.
psql -f db/02_verify.sql

echo
echo "Done. tata_road now holds: $(psql -tAc "SELECT count(*) FROM tata_road.road") roads, $(psql -tAc "SELECT count(*) FROM tata_road.activity") activities, $(psql -tAc "SELECT count(*) FROM tata_road.weekly_entry") weekly entries."
echo "Review the verify output above before trusting any percentage."
echo
echo "Server is NOT started. Start it yourself when ready:"
echo "  python3 -m uvicorn dashboard.main:app --reload --host 0.0.0.0 --port ${PORT}"

