#!/usr/bin/env bash
# ============================================================================
# 02_build.sh — local build entry point (NSS_ERP-style name).
#
# In NSS_ERP this is the from-scratch builder. In TATA that job is already
# done, and done well, by ./rebuild.sh (deps check, Excel -> seed regen,
# verify_seed, apply db/00_schema.sql + db/01_seed.sql + db/02_verify.sql,
# incremental and non-destructive). Rather than duplicate that logic and let
# the two drift apart, this is a thin wrapper that calls rebuild.sh.
#
#   - Local build/rebuild      ->  ./02_build.sh   (this file -> rebuild.sh)
#   - Render build (deploy)    ->  render_build.sh (deps + Neon bootstrap)
#   - Post-build health check  ->  ./03_validate.sh
#
# Any arguments/env are passed straight through (e.g. FORCE=1 ./02_build.sh).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
exec ./rebuild.sh "$@"
