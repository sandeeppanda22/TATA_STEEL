#!/usr/bin/env bash
# ============================================================================
# render_build.sh — Render build command for the Tata Steel Road dashboard.
#
# Mirrors the NSS_ERP build/validate/render pattern, adapted to TATA:
#   - schema is `tata_road` (not nss)
#   - DB is a single DATABASE_URL (not five DB_* vars)
#   - DDL/seed are two flat files (db/00_schema.sql, db/01_seed.sql), not a
#     per-table database/ddl + database/seed tree
#   - no front-end build (plain HTML/JS/CSS; nothing to compile)
#
# What it does, in order:
#   1. Install Python deps.
#   2. If DATABASE_URL is set, EXISTENCE-GATED bootstrap of Neon:
#        - always run db/00_schema.sql (IF NOT EXISTS only: adds MISSING tables)
#        - if the `road` table has 0 rows  -> run db/01_seed.sql
#      Both source files are themselves idempotent (CREATE … IF NOT EXISTS /
#      ON CONFLICT DO NOTHING), so a redeploy against an already-seeded Neon
#      never drops the schema and never touches entered rows.
#
#   CAVEAT (same as a local incremental rebuild): existence-gating is per
#   TABLE, not per column/constraint/seed-row. A redeploy will NOT pick up a
#   changed column, CHECK, or a brand-new seed row on an already-created table
#   — only a deliberate hard rebuild does. This matches the project's
#   no-migrations methodology.
#
# Render runs this as the service `buildCommand`. The start command
# (uvicorn …) is separate — see render.yaml.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "=== [1/2] Installing Python dependencies ==="
pip install -r requirements.txt

# ----------------------------------------------------------------------------
# Database bootstrap. Only runs if DATABASE_URL is present (it always is on
# Render; absent on a bare local `bash render_build.sh`, which then just
# installs deps and stops — exactly what you'd want locally).
# ----------------------------------------------------------------------------
if [ -z "${DATABASE_URL:-}" ]; then
  echo "=== [2/2] DATABASE_URL not set — skipping DB bootstrap (deps only) ==="
  echo "=== build complete (no DB step) ==="
  exit 0
fi

# ----------------------------------------------------------------------------
# SAFETY LOCK. The build never touches the database unless ALLOW_DB_BOOTSTRAP
# is exactly "yes" in the Render environment. It is unset/"no" by default, so
# a normal redeploy (or a mistakenly-pointed DATABASE_URL) cannot create
# schema or load seed rows. To bootstrap a NEW empty DB deliberately: set
# ALLOW_DB_BOOTSTRAP=yes, deploy once, then set it back to "no".
# ----------------------------------------------------------------------------
if [ "${ALLOW_DB_BOOTSTRAP:-no}" != "yes" ]; then
  echo "=== [2/2] ALLOW_DB_BOOTSTRAP != yes — DB bootstrap LOCKED, database not touched ==="
  echo "=== build complete (deps only) ==="
  exit 0
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "WARN: psql not found on PATH — cannot bootstrap Neon from this build."
  echo "      Seed the database by hand (see docs/DEPLOY_RENDER.md Step 2)."
  echo "=== build complete (deps only; DB not touched) ==="
  exit 0
fi

PSQL="psql ${DATABASE_URL} -v ON_ERROR_STOP=1 -X -q"

echo "=== [2/2] Bootstrapping tata_road schema on the target database ==="

# table_exists <name> -> prints t / f
table_exists() {
  psql "${DATABASE_URL}" -X -tA -c \
    "SELECT to_regclass('tata_road.$1') IS NOT NULL;"
}

# row_count <name> -> prints an integer (0 if table is empty)
row_count() {
  psql "${DATABASE_URL}" -X -tA -c "SELECT count(*) FROM tata_road.$1;"
}

# Schema: ALWAYS applied when unlocked. db/00_schema.sql contains only
# CREATE ... IF NOT EXISTS (no DROP/ALTER/INSERT), so this adds tables/indexes
# that are MISSING and leaves every existing table, column and row untouched.
echo "  -> applying db/00_schema.sql (creates only missing tables/indexes)"
$PSQL -f db/00_schema.sql

# Live-refresh triggers/markers: idempotent DDL, touches no row data.
echo "  -> applying db/05_live_change.sql (live-change feed triggers)"
$PSQL -f db/05_live_change.sql

# Seed: only into a brand-new empty DB. A populated road table is never reseeded.
if [ "$(table_exists road)" != "t" ]; then
  echo "ERROR: road table still absent after schema step" >&2
  exit 1
fi

if [ "$(row_count road)" = "0" ]; then
  echo "  -> road table empty: applying db/01_seed.sql"
  $PSQL -f db/01_seed.sql
else
  echo "  -> road table already seeded ($(row_count road) rows): skipping seed"
fi

echo "=== build complete (schema + seed ensured on target DB) ==="
