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

  # Streamable-http MCP requires a session: initialize (capture mcp-session-id),
  # notify initialized, then tools/list. A bare tools/list gets "Missing session ID".
  sid=$(curl -s -D - -o /dev/null -m 8 -X POST "http://127.0.0.1:${port}/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"preflight","version":"1.0"}}}' 2>/dev/null \
    | grep -i '^mcp-session-id:' | tr -d '\r' | cut -d' ' -f2)
  if [ -z "$sid" ]; then
    no "$name not responding on $port from this host"
    continue
  fi
  curl -s -o /dev/null -m 8 -X POST "http://127.0.0.1:${port}/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H "mcp-session-id: ${sid}" \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' 2>/dev/null
  body=$(curl -s -m 8 -X POST "http://127.0.0.1:${port}/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -H "mcp-session-id: ${sid}" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' 2>/dev/null)
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

# identity-service rejects nonce-less mints with IDS_345 "A nonce is required".
NONCE="$(date +%s%N)${RANDOM}${RANDOM}"
JWT=$(curl -s -m 20 "https://${LOGIN_HOST}/identity-service/api/v1/jwt/Token?client_id=idmc_api&nonce=${NONCE}" \
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
    # Two valid contracts exist (docs/IDMC_Console_Runbook.md, repo note):
    #  - current binder contract: $Source$ + $Target$ (what generate_dq_mapping_task
    #    binds today; $Input_Field_Map$ optional)
    #  - runbook 7-param contract: the Phase 2 target, bound via extra_parameters
    RB_MISSING=""
    for p in '$Src_Conn$' '$Src_Object$' '$Tgt_Conn$' '$Tgt_Object$' '$Rule_Spec$' '$Input_Field_Map$' '$Source_Filter$'; do
      printf '%s' "$m" | grep -qF "$p" || RB_MISSING="${RB_MISSING} $p"
    done
    if [ -z "$RB_MISSING" ]; then
      ok "  runbook 7-param contract — all seven parameters present (Phase 2 target; needs Phase 2 binder work before generate_dq_mapping_task binds source/target)"
    elif printf '%s' "$m" | grep -qF '$Source$' && printf '%s' "$m" | grep -qF '$Target$'; then
      ok "  current-binder contract — \$Source\$ + \$Target\$ present; tasks bind today"
      printf '%s' "$m" | grep -qF '$Input_Field_Map$' \
        && ok "  parameter \$Input_Field_Map\$ present" \
        || warn "  \$Input_Field_Map\$ not declared — rule-spec field mapping unavailable; rebuild per docs/IDMC_Console_Runbook.md for the Phase 2 contract"
    else
      no "  template matches NEITHER contract (runbook params missing:${RB_MISSING}; \$Source\$/\$Target\$ also absent) — task creation will fail at bind time"
    fi
  else
    no "template mapping ${TID} does not resolve — step 9 will fail at runtime"
  fi
fi

# ── 3. CDMP credentials ───────────────────────────────────────────────────────
# The search endpoint is POST-only and takes knowledgeQuery as a URL PARAM with
# a {"from","size"} body (a GET gets HTTP 405; a JSON "query" body gets 0 hits).
hdr "3. CDMP — are marketplace credentials valid?"
c=$(cg -o /dev/null -w '%{http_code}' -X POST \
     "${CDGC}/data360/search/v1/assets?knowledgeQuery=%2A&segments=summary" \
     -H 'Content-Type: application/json' -d '{"from":0,"size":1}')
case "$c" in
  200) ok "CDGC/CDMP reachable (HTTP 200) — steps 12-15 should authenticate" ;;
  401|403) no "HTTP $c — this is the UnknownSigner failure. Run scripts/refresh-session.sh, then re-run this check" ;;
  *) warn "HTTP $c — unexpected; verify manually before relying on steps 12-15" ;;
esac

# ── 4. Relationship Discovery / lineage ───────────────────────────────────────
hdr "4. Lineage — has Relationship Discovery populated dataflow for ${DEMO_TABLE}?"
# Hits carry core.identity (not "id"); prefer a relational Table over the
# governance DataSet twin. GET-by-id needs scheme=internal or it returns {}.
aid=$(cg -X POST "${CDGC}/data360/search/v1/assets?knowledgeQuery=${DEMO_TABLE}&segments=summary,systemAttributes" \
      -H 'Content-Type: application/json' -d '{"from":0,"size":10}' 2>/dev/null \
  | python -c "
import json,sys
try: hits = json.load(sys.stdin).get('hits') or []
except Exception: hits = []
if isinstance(hits, dict): hits = hits.get('hits') or []
tables = [h for h in hits if 'Table' in str((h.get('systemAttributes') or {}).get('core.classType',''))]
pick = (tables or hits or [{}])[0]
print(pick.get('core.identity') or '')" 2>/dev/null)
if [ -z "$aid" ]; then
  no "${DEMO_TABLE} not found in the catalog — check 4 and 5 cannot run"
else
  ok "resolved ${DEMO_TABLE} (${aid})"
  edges=$(cg "${CDGC}/data360/search/v1/assets/${aid}?scheme=internal&segments=summary,lineage-direction:all,lineage-level:dataset,lineage-distance:3" 2>/dev/null \
    | python -c "
import json,sys
try: print(len(json.load(sys.stdin).get('lineage') or []))
except Exception: print(0)" 2>/dev/null)
  if [ "${edges:-0}" -gt 0 ]; then ok "lineage returns $edges edges — step 2 will render"
  else no "lineage is EMPTY. Either Relationship Discovery has not run on this catalog source, or no CDI mappings touch this table. Step 2's lineage substeps will work and show nothing. Demo a table with real lineage, or run an MCC scan with the Relationship Discovery capability."
  fi
fi

# ── 5. DQ score history ───────────────────────────────────────────────────────
hdr "5. Score history — will the trend chart have more than one point?"
if [ -n "${aid:-}" ]; then
  # The dataQuality:all segment returns DQResult objects; core.scoreTrend on one
  # means CDGC has computed a trend (≥2 historical points behind it).
  read -r results trends <<EOF2
$(cg "${CDGC}/data360/search/v1/assets/${aid}?scheme=internal&segments=dataQuality:all" 2>/dev/null \
  | python -c "
import json,sys
try: dq = json.load(sys.stdin).get('dataQuality') or []
except Exception: dq = []
print(len(dq), sum(1 for e in dq if isinstance(e, dict) and e.get('core.scoreTrend') is not None))" 2>/dev/null)
EOF2
  if   [ "${trends:-0}" -gt 0 ]; then ok "${results} DQ result(s), ${trends} with a computed score trend — trend will render"
  elif [ "${results:-0}" -gt 0 ]; then warn "${results} DQ result(s) but no computed trend — check_score_trends draws a flat line. Backfill with propagate_dq_score, which takes run_date, dimension and row counts."
  else no "no score history — step 11 trends will be empty. Backfill several days with propagate_dq_score before the demo."
  fi
fi

printf '\n\033[1mpass %s · fail %s · needs eyes %s\033[0m\n' "$PASS" "$FAIL" "$WARN"
[ "$FAIL" -gt 0 ] && echo "Fix FAILs before writing code — each one invalidates a phase." && exit 1
exit 0
