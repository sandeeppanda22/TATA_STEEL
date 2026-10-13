#!/usr/bin/env bash
# ============================================================================
# 03_validate.sh — read-only health check of the tata_road database.
#
# Mirrors the NSS_ERP validate script, adapted to TATA. Runs NO DDL and NO
# seed — it only inspects the target DB and reports pass / warn / fail. Use it
# after a build (local or on Neon) to confirm the data looks right.
#
# Target DB:
#   - If DATABASE_URL is set, validates that (e.g. Neon).
#   - Otherwise falls back to local libpq defaults (PG* env / Unix socket),
#     matching rebuild.sh: PGUSER=postgres, PGDATABASE=tata_steel, no host.
#
# Exit code: 0 if there are no hard failures (warnings are OK), 1 otherwise.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")"

# ---- colours (only when stdout is a terminal; CI / piped logs stay plain) ---
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_CYAN=$'\033[36m'
else
  C_RESET=''; C_BOLD=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_CYAN=''
fi

# ---- load local DB credentials from .env (the same file rebuild.sh writes) --
# So once you've run ./rebuild.sh — or answered the one prompt below — neither
# the build nor this validator ever asks for the password again. A real env var
# already set always wins over the file.
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r key val; do
    case "$key" in
      PGHOST|PGPORT|PGUSER|PGDATABASE|PGPASSWORD|PGGSSENCMODE|DATABASE_URL)
        val="${val%\"}"; val="${val#\"}"
        if [ -z "$(eval "echo \${$key:-}")" ]; then export "$key=$val"; fi
        ;;
    esac
  done < "$ENV_FILE"
fi

# ---- connection -------------------------------------------------------------
if [ -n "${DATABASE_URL:-}" ]; then
  # Neon / remote: the password lives inside the URL — nothing to prompt for.
  PSQL=(psql "${DATABASE_URL}" -X -tA)
  TARGET="DATABASE_URL"
else
  export PGUSER="${PGUSER:-postgres}"
  export PGDATABASE="${PGDATABASE:-tata_steel}"
  export PGPORT="${PGPORT:-5432}"
  export PGGSSENCMODE="${PGGSSENCMODE:-disable}"
  [ -n "${PGHOST:-}" ] && export PGHOST
  # Ask for the password ONCE per run (not on every psql check below) and export
  # it, mirroring rebuild.sh. Skipped entirely if PGPASSWORD, .env, or ~/.pgpass
  # already covers it.
  if [ -z "${PGPASSWORD:-}" ] && [ ! -f "$HOME/.pgpass" ]; then
    printf '%sPassword for %s@%s:%s ' "$C_CYAN" "$PGUSER" "${PGHOST:-local socket}" "$C_RESET" >&2
    read -rs PGPASSWORD
    printf '\n' >&2
    export PGPASSWORD
  fi
  PSQL=(psql -X -tA)
  TARGET="local (${PGUSER}@${PGDATABASE})"
fi

# q <sql> -> prints single scalar result (empty string on error)
q() { "${PSQL[@]}" -c "$1" 2>/dev/null; }

PASS=0; WARN=0; FAIL=0
log_pass() { PASS=$((PASS+1)); echo "  ${C_GREEN}PASS${C_RESET}  $1"; }
log_warn() { WARN=$((WARN+1)); echo "  ${C_YELLOW}WARN${C_RESET}  $1"; }
log_fail() { FAIL=$((FAIL+1)); echo "  ${C_RED}FAIL${C_RESET}  $1"; }

echo "${C_BOLD}=== Validating tata_road schema on: ${TARGET} ===${C_RESET}"

# ---- 0. can we even connect? ------------------------------------------------
if [ "$(q "SELECT 1;")" != "1" ]; then
  echo "  ${C_RED}FAIL${C_RESET}  cannot connect to the database (check DATABASE_URL / PG* env)"
  echo "${C_BOLD}=== ${C_GREEN}0 pass${C_RESET}${C_BOLD} / ${C_YELLOW}0 warn${C_RESET}${C_BOLD} / ${C_RED}1 fail${C_RESET}${C_BOLD} ===${C_RESET}"
  exit 1
fi

# ---- 1. table existence -----------------------------------------------------
for t in road activity weekly_entry data_issue edit_log manual_issue; do
  if [ "$(q "SELECT to_regclass('tata_road.$t') IS NOT NULL;")" = "t" ]; then
    log_pass "table $t exists"
  else
    log_fail "table $t is MISSING"
  fi
done

# ---- 2. row counts (expected after a clean seed: 17 / 177 / 10) -------------
roads="$(q "SELECT count(*) FROM tata_road.road;")"
acts="$(q "SELECT count(*) FROM tata_road.activity;")"
issues="$(q "SELECT count(*) FROM tata_road.data_issue;")"
weekly="$(q "SELECT count(*) FROM tata_road.weekly_entry;")"

[ "$roads" = "17" ]  && log_pass "road count = 17" \
                     || log_warn "road count = ${roads:-?} (expected 17 at clean seed; differs if roads were added/edited)"
[ "$acts" = "177" ]  && log_pass "activity count = 177" \
                     || log_warn "activity count = ${acts:-?} (expected 177 at clean seed)"
[ "$issues" = "10" ] && log_pass "data_issue count = 10" \
                     || log_warn "data_issue count = ${issues:-?} (expected 10 at clean seed)"
log_pass "weekly_entry count = ${weekly:-?} (seeded empty; grows as actuals are entered)"

# ---- 3. referential integrity (orphan rows) --------------------------------
# activity -> road
orphan_act="$(q "SELECT count(*) FROM tata_road.activity a
                 LEFT JOIN tata_road.road r ON r.road_no = a.road_no
                 WHERE r.road_no IS NULL;")"
[ "$orphan_act" = "0" ] && log_pass "no orphan activity rows (activity->road OK)" \
                        || log_fail "${orphan_act} activity rows reference a missing road"

# weekly_entry -> activity
orphan_we="$(q "SELECT count(*) FROM tata_road.weekly_entry w
                LEFT JOIN tata_road.activity a ON a.activity_id = w.activity_id
                WHERE a.activity_id IS NULL;")"
[ "$orphan_we" = "0" ] && log_pass "no orphan weekly_entry rows (weekly_entry->activity OK)" \
                       || log_fail "${orphan_we} weekly_entry rows reference a missing activity"

# ---- 4. weekly_entry weeks must start Monday (DDL CHECK, re-verified) -------
bad_mon="$(q "SELECT count(*) FROM tata_road.weekly_entry
              WHERE EXTRACT(ISODOW FROM week_start_date) <> 1;")"
[ "${bad_mon:-0}" = "0" ] && log_pass "all weekly_entry weeks start on Monday" \
                          || log_fail "${bad_mon} weekly_entry rows have a non-Monday week_start_date"

# ---- 5. BR-05 per-road weight total = 100 (road 10 = 130 is a known defect)--
bad_wt="$(q "SELECT string_agg(road_no::text || '=' || tot::text, ', ')
             FROM (SELECT road_no, round(sum(weight_pct)) AS tot
                   FROM tata_road.activity GROUP BY road_no
                   HAVING round(sum(weight_pct)) <> 100) s;")"
if [ -z "$bad_wt" ]; then
  log_pass "every road's activity weights total 100 (BR-05)"
elif [ "$bad_wt" = "10=130" ]; then
  log_warn "road 10 weights total 130 — known unresolved defect (needs ruling); all other roads OK"
else
  log_warn "roads with weight total <> 100: ${bad_wt} (road 10=130 is the known defect)"
fi

echo "${C_BOLD}=== ${C_GREEN}${PASS} pass${C_RESET}${C_BOLD} / ${C_YELLOW}${WARN} warn${C_RESET}${C_BOLD} / ${C_RED}${FAIL} fail${C_RESET}${C_BOLD} ===${C_RESET}"
[ "$FAIL" -eq 0 ]
