#!/usr/bin/env bash
# Pre-demo environment checks. Run from the repo root, with .env populated.
#   bash preflight.sh                    # checks 1-5
#   DEMO_TABLE=CUSTOMER_POSITIONS bash preflight.sh
#
# Checks 4 and 5 need a table name that will actually be demoed.
# Nothing here writes. Safe to re-run.

set -uo pipefail
[ -f .env ] && set -a && . ./.env && set +a

DEMO_TABLE="${DEMO_TABLE:-CUSTOMER_POSITIONS}"
PASS=0; FAIL=0; WARN=0
ok(){   printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
no(){   printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
warn(){ printf '  \033[33mCHECK\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
hdr(){  printf '\n\033[1m%s\033[0m\n' "$1"; }

# ── 1. Four MCP servers ───────────────────────────────────────────────────────
hdr "1. MCP servers — are the four new ones serving?"

grep -qE '127\.0\.0\.1:(8766|8767|8768|8769)' docker-compose.yml 2>/dev/null \
  && no "docker-compose.yml points a server URL at 127.0.0.1 — inside the UI container that resolves to itself, not to the server. Use service names, e.g. http://lineage-reporter:8766/mcp" \
  || ok "no 127.0.0.1 server URLs in docker-compose.yml"

for svc in lineage-reporter:8766 glossary-manager:8767 dq-monitor:8768 data-onboarding:8769; do
  name="${svc%%:*}"; port="${svc##*:}"
  grep -q "$name" docker-compose.yml 2>/dev/null \
    && ok "$name declared in docker-compose.yml" \
    || no "$name missing from docker-compose.yml"

  # tools/list is the cheapest real MCP call. A 200 with a tools array means serving.
  body=$(curl -s -m 8 -X POST "http://127.0.0.1:${port}/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' 2>/dev/null)
  if printf '%s' "$body" | grep -q '"tools"'; then
    n=$(printf '%s' "$body" | grep -o '"name"' | wc -l | tr -d ' ')
    ok "$name responding on $port — $n tools"
  else
    no "$name not responding on $port from this host"
  fi
done
echo "        If run outside the container, ports may not be published. Re-run inside:"
echo "        az containerapp exec -n <app> -g \$AZURE_RESOURCE_GROUP --command /bin/sh"

# ── auth ──────────────────────────────────────────────────────────────────────
hdr "auth — v2 session + JWT"
LOGIN_HOST="${IDMC_LOGIN_HOST:-dmp-us.informaticacloud.com}"
resp=$(curl -s -m 20 -X POST "https://${LOGIN_HOST}/ma/api/v2/user/login" \
  -H 'Content-Type: application/json' \
  -d "{\"@type\":\"login\",\"username\":\"${IDMC_USER:-}\",\"password\":\"${IDMC_PASS:-}\"}" 2>/dev/null)
SESSION=$(printf '%s' "$resp" | grep -o '"icSessionId"[^,]*' | cut -d'"' -f4)
SERVER=$(printf '%s' "$resp"  | grep -o '"serverUrl"[^,]*'   | cut -d'"' -f4)
if [ -n "$SESSION" ]; then ok "v2 login ok"; else no "v2 login failed — checks 2-5 cannot run"; fi

JWT=$(curl -s -m 20 "https://${LOGIN_HOST}/identity-service/api/v1/jwt/Token?client_id=idmc_api" \
  -H "IDS-SESSION-ID: ${SESSION}" 2>/dev/null | grep -o '"jwt_token"[^,]*' | cut -d'"' -f4)
[ -n "$JWT" ] && ok "JWT minted" || no "JWT mint failed — CDGC checks below will fail"

CDGC="${CDGC_API_BASE:-https://cdgc-api.dm-us.informaticacloud.com}"
cg(){ curl -s -m 25 -H "Authorization: Bearer ${JWT}" -H "IDS-SESSION-ID: ${SESSION}" \
        -H "X-INFA-ORG-ID: ${IDMC_ORG_ID:-}" -H 'x-infa-product-id: CDGC' "$@"; }

# ── 2. M_DQ_Generic ───────────────────────────────────────────────────────────
hdr "2. M_DQ_Generic — does the DQ execution template exist?"
TID="${IDMC_DQ_TEMPLATE_MAPPING_ID:-}"
if [ -z "$TID" ]; then
  no "IDMC_DQ_TEMPLATE_MAPPING_ID not set in .env — step 9 has no template to bind to"
else
  m=$(curl -s -m 20 -H "icSessionId: ${SESSION}" "${SERVER}/api/v2/mapping/${TID}" 2>/dev/null)
  if printf '%s' "$m" | grep -q '"name"'; then
    nm=$(printf '%s' "$m" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4)
    ok "template mapping resolves: $nm"
    for p in Src_Conn Src_Object Tgt_Conn Tgt_Object Rule_Spec Input_Field_Map Source_Filter; do
      printf '%s' "$m" | grep -q "$p" && ok "  parameter $p present" || no "  parameter $p MISSING — task creation will fail at bind time"
    done
  else
    no "template mapping ${TID} does not resolve — step 9 will fail at runtime"
  fi
fi

# ── 3. CDMP credentials ───────────────────────────────────────────────────────
hdr "3. CDMP — are marketplace credentials valid?"
c=$(cg -o /dev/null -w '%{http_code}' "${CDGC}/data360/search/v1/assets?q=*&limit=1")
case "$c" in
  200) ok "CDGC/CDMP reachable (HTTP 200) — steps 12-15 should authenticate" ;;
  401|403) no "HTTP $c — this is the UnknownSigner failure. Run scripts/refresh-session.sh, then re-run this check" ;;
  *) warn "HTTP $c — unexpected; verify manually before relying on steps 12-15" ;;
esac

# ── 4. Relationship Discovery / lineage ───────────────────────────────────────
hdr "4. Lineage — has Relationship Discovery populated dataflow for ${DEMO_TABLE}?"
aid=$(cg -X POST "${CDGC}/data360/search/v1/assets" -H 'Content-Type: application/json' \
      -d "{\"query\":\"${DEMO_TABLE}\",\"limit\":1}" 2>/dev/null | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$aid" ]; then
  no "${DEMO_TABLE} not found in the catalog — check 4 and 5 cannot run"
else
  ok "resolved ${DEMO_TABLE}"
  lin=$(cg "${CDGC}/data360/search/v1/assets/${aid}?segments=lineage-direction:ALL" 2>/dev/null)
  edges=$(printf '%s' "$lin" | grep -o '"from"' | wc -l | tr -d ' ')
  if [ "$edges" -gt 0 ]; then ok "lineage returns $edges edges — step 2 will render"
  else no "lineage is EMPTY. Either Relationship Discovery has not run on this catalog source, or no CDI mappings touch this table. Step 2's lineage substeps will work and show nothing. Demo a table with real lineage, or run an MCC scan with the Relationship Discovery capability."
  fi
fi

# ── 5. DQ score history ───────────────────────────────────────────────────────
hdr "5. Score history — will the trend chart have more than one point?"
if [ -n "${aid:-}" ]; then
  dq=$(cg "${CDGC}/data360/search/v1/assets/${aid}?segments=dataQuality:all" 2>/dev/null)
  pts=$(printf '%s' "$dq" | grep -o '"runDate"\|"run_date"\|"scoreDate"' | wc -l | tr -d ' ')
  if   [ "$pts" -ge 3 ]; then ok "$pts score points — trend will render"
  elif [ "$pts" -gt 0 ]; then warn "$pts score point(s) — check_score_trends draws a flat line. Backfill with propagate_dq_score, which takes run_date, dimension and row counts."
  else no "no score history — step 11 trends will be empty. Backfill several days with propagate_dq_score before the demo."
  fi
fi

printf '\n\033[1mpass %s · fail %s · needs eyes %s\033[0m\n' "$PASS" "$FAIL" "$WARN"
[ "$FAIL" -gt 0 ] && echo "Fix FAILs before writing code — each one invalidates a phase." && exit 1
exit 0
