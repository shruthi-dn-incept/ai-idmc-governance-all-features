"""governance_ui.py — Branded wizard UI for the IDMC governance pipeline.

Serves a React frontend on http://127.0.0.1:8080 and bridges REST calls
to the MCP servers (defaults; override via *_URL env vars or .env):
  ai-governance     :8770
  governance-engine :8765
  lineage-reporter  :8766
  glossary-manager  :8767
  dq-monitor        :8768
  data-onboarding   :8769

Usage:
  python -m idmc_governance.ui.app
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel

from idmc_governance.common.paths import load_env_file

# The MCP servers read the repo-root .env into their environment; the UI must do
# the same or its server URLs / port disagree with where the servers actually
# bind (e.g. a local .env pinning 9765/9770). Process env still wins.
load_env_file()

AI_GOVERNANCE_URL     = os.getenv("AI_GOVERNANCE_URL",     "http://127.0.0.1:8770/mcp")
GOVERNANCE_ENGINE_URL = os.getenv("GOVERNANCE_ENGINE_URL", "http://127.0.0.1:8765/mcp")
LINEAGE_REPORTER_URL  = os.getenv("LINEAGE_REPORTER_URL",  "http://127.0.0.1:8766/mcp")
GLOSSARY_MANAGER_URL  = os.getenv("GLOSSARY_MANAGER_URL",  "http://127.0.0.1:8767/mcp")
DQ_MONITOR_URL        = os.getenv("DQ_MONITOR_URL",        "http://127.0.0.1:8768/mcp")
DATA_ONBOARDING_URL   = os.getenv("DATA_ONBOARDING_URL",   "http://127.0.0.1:8769/mcp")

# All six servers, keyed by display name — drives /api/health and the UI header.
MCP_SERVERS: dict[str, str] = {
    "ai_governance":     AI_GOVERNANCE_URL,
    "governance_engine": GOVERNANCE_ENGINE_URL,
    "lineage_reporter":  LINEAGE_REPORTER_URL,
    "glossary_manager":  GLOSSARY_MANAGER_URL,
    "dq_monitor":        DQ_MONITOR_URL,
    "data_onboarding":   DATA_ONBOARDING_URL,
}

def _read_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path(__file__).resolve().parents[3] / ".env"  # repo-root .env
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("governance_ui")

app = FastAPI(title="INCEPT Data Governance")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ── MCP helpers ───────────────────────────────────────────────────────────────

def _unwrap_exception(exc: BaseException) -> BaseException:
    """Recursively unwrap ExceptionGroup to get the root cause."""
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


async def _call(server_url: str, tool: str, args: dict) -> Any:
    try:
        async with streamablehttp_client(server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
                if result.isError:
                    raise RuntimeError(str(result.content))
                text = result.content[0].text if result.content else "{}"
                return json.loads(text)
    except* Exception as eg:
        raise _unwrap_exception(eg.exceptions[0]) from None


async def _govern(request: str, step: str | None = None) -> dict:
    # `step` forces deterministic dispatch on the server (no LLM step-inference), so a
    # stale persisted state can never misroute an explicit UI action to the wrong step.
    args: dict = {"request": request}
    if step:
        args["step"] = step
    return await _call(AI_GOVERNANCE_URL, "govern", args)


# Session expiry surfaces as 401/403 with REPO_38205 buried in a tool error
# string. Map it to HTTP 401 so the frontend's shared interceptor (cross-cutting
# requirement 1) raises the reconnect banner instead of a raw 500.
_SESSION_EXPIRY_MARKERS = ("repo_38205", "http 401", "unauthorized", "unknownsigner",
                           "session expired", "invalid session", "session is not valid")


def _as_http_error(e: Exception) -> HTTPException:
    msg = str(e)
    low = msg.lower()
    if any(m in low for m in _SESSION_EXPIRY_MARKERS):
        return HTTPException(
            status_code=401,
            detail=f"IDMC session expired — re-authenticate and retry. ({msg[:300]})",
        )
    return HTTPException(status_code=500, detail=msg)


async def _bridge(server_url: str, tool: str, args: dict) -> Any:
    """Call an MCP tool, translating failures into session-aware HTTP errors."""
    try:
        return await _call(server_url, tool, args)
    except HTTPException:
        raise
    except Exception as e:
        raise _as_http_error(e) from None


# ── Health: poll all six servers, name the ones that are down ────────────────
# During a demo, a silent failure is worse than a visible one — but so is a
# false alarm. A long-running tool call (e.g. a 6-minute catalog browse) can
# starve a server's event loop so the MCP handshake times out even though the
# process is alive and working. If the port still accepts TCP, that server is
# BUSY, not down.

async def _probe_server(name: str, url: str) -> dict:
    async def _ping():
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

    try:
        await asyncio.wait_for(_ping(), timeout=4)
        return {"server": name, "url": url, "ok": True, "state": "online"}
    except (Exception, BaseExceptionGroup) as e:  # anyio wraps failures in groups
        root = _unwrap_exception(e)
        parts = urlsplit(url)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(parts.hostname, parts.port or 80), timeout=2)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return {"server": name, "url": url, "ok": True, "state": "busy",
                    "note": "processing a long-running call"}
        except Exception:  # noqa: BLE001
            return {"server": name, "url": url, "ok": False, "state": "down",
                    "error": (str(root) or type(root).__name__)[:200]}


@app.get("/api/health")
async def health():
    results = await asyncio.gather(*[_probe_server(n, u) for n, u in MCP_SERVERS.items()])
    down = [r["server"] for r in results if r["state"] == "down"]
    busy = [r["server"] for r in results if r["state"] == "busy"]
    return {"servers": list(results), "down": down, "busy": busy, "all_ok": not down,
            "total": len(results), "online": len(results) - len(down)}


@app.post("/api/reset")
async def reset_session():
    """Clear server-side pipeline state so 'New Session' truly starts fresh."""
    try:
        return await _call(AI_GOVERNANCE_URL, "reset_pipeline", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    env = _read_env_file()
    return {
        "dmp_collection_id": env.get("DMP_COLLECTION_ID", ""),
    }


# ── Route ↔ ladder-step mapping ───────────────────────────────────────────────
# Route names predate the 15-step ladder and are kept for compatibility
# (documenting rather than renaming, per spec "Route naming drift"):
#
#   ladder step                    route
#   ─────────────────────────────  ──────────────────────────────
#    1 Discover Catalog            /api/step/discover
#    2 Scan Table                  /api/step/scan
#    3 Profile Data                /api/step/profile             (stub — Phase 1)
#    4 Generate Taxonomy           /api/step/taxonomy
#    5 Domain Structure            /api/step/domain_structure (+ /system_dataset substep)
#    6 Curate Columns              /api/step/curate
#    7 Recommend Rules             /api/step/recommend_rules     (stub — Phase 1)
#    8 Create DQ Rules             /api/step/dq_rules            (route name: dq_rules)
#    9 Schedule Execution          /api/step/schedule_execution  (stub — Phase 2)
#   10 Publish to Catalog          /api/step/mcc_scan            (route name: mcc_scan)
#                                  + /api/step/scores (upload_dq_scores)
#                                  + /api/step/propagate_dq_score
#   11 Monitor Quality             /api/step/monitor_quality     (stub — Phase 3)
#   12 Create Collection           /api/step/create_collection   (composite of 4 tools)
#   13 Publish to Marketplace      /api/step/publish_marketplace
#   14 Configure Delivery          /api/step/configure_delivery  (composite of 4 tools)
#   15 Consumer Access             /api/step/consumer_access
#                                  + /approve_order /verify_access /withdraw_access
#
# /api/step/data_quality (dq_rules + scores in one call) predates the ladder’s
# split of rules (step 8) from score upload (step 10); it stays for the run-all
# path but no ladder step binds to it directly.


# ── Stub routes: ladder steps whose phases have not landed ────────────────────
# Each returns 501 so the ladder renders complete — a rung that says "next
# release" instead of a dead rung. The owning phase replaces the stub in place.

_STUB_STEPS = {
    "profile":            ("Profile Data",        "Phase 1"),
    "recommend_rules":    ("Recommend Rules",     "Phase 1"),
    "schedule_execution": ("Schedule Execution",  "Phase 2"),
}


def _stub_response(step_key: str):
    label, phase = _STUB_STEPS[step_key]
    raise HTTPException(
        status_code=501,
        detail=f"{label} ships in the next release ({phase}). "
               f"The MCP tools behind it are deployed and reachable; this step's "
               f"screen is not built yet.",
    )


@app.post("/api/step/profile")
async def step_profile_stub():
    _stub_response("profile")


@app.post("/api/step/recommend_rules")
async def step_recommend_rules_stub():
    _stub_response("recommend_rules")


@app.post("/api/step/schedule_execution")
async def step_schedule_execution_stub():
    _stub_response("schedule_execution")


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4 — lineage substeps on step 2 (lineage_reporter)
# ══════════════════════════════════════════════════════════════════════════════

class LineageRequest(BaseModel):
    asset_name: str
    direction: str = "all"      # upstream | downstream | all (server normalises)
    depth: int = 3
    level: str = "dataset"


@app.post("/api/step/trace_lineage")
async def step_trace_lineage(req: LineageRequest):
    args = req.model_dump()
    args["depth"] = max(1, min(args["depth"], 5))   # each hop is another sequential call
    return await _bridge(LINEAGE_REPORTER_URL, "trace_lineage", args)


class ImpactRequest(BaseModel):
    asset_name: str
    change_description: str = ""
    depth: int = 3
    level: str = "dataset"


@app.post("/api/step/generate_impact_report")
async def step_generate_impact_report(req: ImpactRequest):
    args = req.model_dump()
    args["depth"] = max(1, min(args["depth"], 5))
    args["change_description"] = args["change_description"] or "Proposed change (unspecified)"
    return await _bridge(LINEAGE_REPORTER_URL, "generate_impact_report", args)


class SourceFinderRequest(BaseModel):
    asset_name: str
    depth: int = 5
    level: str = "dataset"


@app.post("/api/step/find_data_source")
async def step_find_data_source(req: SourceFinderRequest):
    args = req.model_dump()
    args["depth"] = max(1, min(args["depth"], 5))
    return await _bridge(LINEAGE_REPORTER_URL, "find_data_source", args)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 5 — glossary substeps on step 6 (glossary_manager)
# ══════════════════════════════════════════════════════════════════════════════

class SuggestTermsRequest(BaseModel):
    asset_name: str
    domain_context: str | None = None


@app.post("/api/step/suggest_terms_for_asset")
async def step_suggest_terms(req: SuggestTermsRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GLOSSARY_MANAGER_URL, "suggest_terms_for_asset", args)


class CreateTermRequest(BaseModel):
    term_name: str
    definition: str
    category: str | None = None
    synonyms: list[str] = []


@app.post("/api/step/create_glossary_term")
async def step_create_glossary_term(req: CreateTermRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GLOSSARY_MANAGER_URL, "create_glossary_term", args)


class GlossaryHealthRequest(BaseModel):
    scan_scope: str = "all"
    sample_size: int = 200
    min_definition_length: int = 20


@app.post("/api/step/detect_glossary_issues")
async def step_detect_glossary_issues(req: GlossaryHealthRequest = GlossaryHealthRequest()):
    return await _bridge(GLOSSARY_MANAGER_URL, "detect_glossary_issues", req.model_dump())


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — step 11 Monitor Quality (dq_monitor). Parent = scorecard.
# ══════════════════════════════════════════════════════════════════════════════

class ScoresRequest(BaseModel):
    asset_name: str
    dimension: str | None = None


@app.post("/api/step/monitor_quality")
async def step_monitor_quality(req: ScoresRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(DQ_MONITOR_URL, "get_dq_scores", args)


class TrendsRequest(BaseModel):
    asset_name: str
    lookback_days: int = 30
    degradation_delta: float = 10.0


@app.post("/api/step/check_score_trends")
async def step_check_score_trends(req: TrendsRequest):
    return await _bridge(DQ_MONITOR_URL, "check_score_trends", req.model_dump())


class RemediationRequest(BaseModel):
    asset_name: str


@app.post("/api/step/recommend_remediation")
async def step_recommend_remediation(req: RemediationRequest):
    return await _bridge(DQ_MONITOR_URL, "recommend_remediation", req.model_dump())


class AlertCreateRequest(BaseModel):
    asset_name: str
    threshold: float
    notify_email: str
    dimension: str | None = None
    lookback_days: int = 30
    note: str = ""


@app.post("/api/step/alert_on_degradation")
async def step_alert_on_degradation(req: AlertCreateRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(DQ_MONITOR_URL, "alert_on_degradation", args)


@app.get("/api/monitor/alerts")
async def monitor_list_alerts():
    """Configured alert rules — stored by OUR platform (.dq_monitor_alerts.json),
    evaluated by our scheduler. CDGC has no alert-registration API."""
    candidates = [_REPO_ROOT / "state" / ".dq_monitor_alerts.json",
                  _REPO_ROOT / ".dq_monitor_alerts.json"]
    p = next((c for c in candidates if c.exists()), candidates[-1])
    alerts = []
    if p.exists():
        try:
            alerts = json.loads(p.read_text() or "[]")
        except Exception:  # noqa: BLE001
            alerts = []
    return {"alerts": alerts, "count": len(alerts), "alerts_path": str(p),
            "storage": "local — evaluated by our scheduler, not registered in CDGC"}


# ── Profile session state (spec Phase 0 item 8) ───────────────────────────────
# Profiling runs once at step 3; steps 4 (taxonomy) and 7 (recommend rules) read
# the stored profile rather than re-running the most expensive operation in the
# ladder. The UI backend owns this store because the producer (governance_engine)
# and the consumers (ai_governance) are different servers and neither should
# depend on a workflow concern that exists only because the UI sequences the
# steps. Phase 1 defines the consumption shape.

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROFILE_STATE_PATH = _REPO_ROOT / "state" / "profile_state.json"


def _profile_key(connection: str, schema: str, table: str) -> str:
    return f"{connection or '-'}/{schema or '-'}/{table}"


def _read_profile_state() -> dict:
    if not _PROFILE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(_PROFILE_STATE_PATH.read_text() or "{}")
    except Exception:  # noqa: BLE001
        return {}


class ProfileStateEntry(BaseModel):
    connection: str = ""
    schema: str = ""
    table: str
    profile: dict


@app.get("/api/profile_state")
async def get_profile_state(connection: str = "", schema: str = "", table: str = ""):
    state = _read_profile_state()
    if table:
        entry = state.get(_profile_key(connection, schema, table))
        return {"found": entry is not None, "entry": entry}
    return {"count": len(state), "keys": sorted(state.keys())}


@app.post("/api/profile_state")
async def put_profile_state(req: ProfileStateEntry):
    state = _read_profile_state()
    key = _profile_key(req.connection, req.schema, req.table)
    state[key] = {
        "connection": req.connection,
        "schema":     req.schema,
        "table":      req.table,
        "saved_at":   _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "profile":    req.profile,
    }
    _PROFILE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_STATE_PATH.write_text(json.dumps(state, indent=2))
    return {"saved": key, "count": len(state)}


# ── Step 1: Discover ──────────────────────────────────────────────────────────

@app.post("/api/step/discover")
async def step_discover():
    try:
        raw = await _call(AI_GOVERNANCE_URL, "list_catalog_tables", {
            "max_results":     5000,
            "group_by_source": True,
        })
        catalog_sources = raw.get("catalog_sources_grouped", [])
        tables_for_selection = [
            {"name": t["name"], "schema": s["schema"], "source": cs["source"]}
            for cs in catalog_sources
            for s in cs.get("schemas", [])
            for t in s.get("tables", [])
        ]
        raw["tables_for_selection"]     = tables_for_selection
        raw["awaiting_table_selection"] = True
        return {
            "step":      "list_catalog",
            "reasoning": "Listing GOVTEST catalog sources with sample tables from CDGC",
            "result":    raw,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 2: Scan ──────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    table: str
    schema: str
    scan_all: bool = False
    table_names: list[str] = []       # pre-resolved names from discover results (for scan_all)
    total_tables_in_schema: int = 0   # total tables in the selected schema (for time estimate)


@app.post("/api/step/scan")
async def step_scan(req: ScanRequest):
    try:
        # Resolve table names for this schema — bypass LLM govern routing to prevent
        # session state from a previous scan overriding the user's current selection.
        if req.scan_all or not req.table:
            # Use pre-resolved table names sent by the frontend from discover results —
            # avoids a second CDGC round-trip that can return 0 due to session/relevance caps.
            table_names = req.table_names[:10] if req.table_names else []
        else:
            table_names = [req.table]

        if not table_names:
            return {
                "scan": {"step": "scan", "result": {"found_count": 0, "missing": [], "tables": [], "next_actions": []}},
                "columns": [],
            }

        t0 = _time.monotonic()

        find_result = await _call(AI_GOVERNANCE_URL, "scan_find_tables", {
            "table_names": table_names,
            "schema_hint": req.schema,
        })
        fetch_actions = [
            a for a in find_result.get("next_actions", [])
            if a.get("tool") == "scan_fetch_columns"
        ]

        async def _fetch_one(p: dict) -> dict:
            return await _call(AI_GOVERNANCE_URL, "scan_fetch_columns", {
                "table_name":  p["table_name"],
                "table_id":    p["table_id"],
                "schema":      p.get("schema", ""),
                "external_id": p.get("external_id", ""),
            })

        columns = list(await asyncio.gather(*[_fetch_one(a["params"]) for a in fetch_actions]))

        elapsed         = round(_time.monotonic() - t0, 1)
        tables_scanned  = len([c for c in columns if c])
        total_in_schema = req.total_tables_in_schema or 0
        per_table_s     = (elapsed / tables_scanned) if tables_scanned > 0 else 0
        est_full_min    = round(per_table_s * total_in_schema / 60, 1) if total_in_schema > 0 else 0

        return {
            "scan":                  {"step": "scan", "result": find_result},
            "columns":               columns,
            "elapsed_seconds":       elapsed,
            "tables_scanned":        tables_scanned,
            "total_in_schema":       total_in_schema,
            "estimated_full_minutes": est_full_min,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Full-schema time estimate (shared by steps 2-6) ───────────────────────────
def _estimate_block(elapsed: float, sample_tables: int, total_tables: int,
                    fixed_cost: bool = False) -> dict:
    """Full-schema runtime estimate from a sample run.

    Per-table steps (scan/taxonomy/curate/dq): full = (elapsed / sample) * total.
    Fixed-cost steps (domain structure — a one-time hierarchy write that does NOT
    grow with table count): the full-schema time is just the observed elapsed, and
    fixed_cost=True tells the UI to show elapsed only (no table extrapolation).
    Returned fields match the Scan step so the UI renders them uniformly.
    """
    if fixed_cost:
        return {
            "elapsed_seconds":        round(elapsed, 1),
            "sample_tables":          sample_tables,
            "total_in_schema":        total_tables,
            "estimated_full_minutes": round(elapsed / 60, 1),
            "fixed_cost":             True,
        }
    per = (elapsed / sample_tables) if sample_tables > 0 else 0.0
    est_min = round(per * total_tables / 60, 1) if (total_tables > 0 and sample_tables > 0) else 0
    return {
        "elapsed_seconds":        round(elapsed, 1),
        "sample_tables":          sample_tables,
        "total_in_schema":        total_tables,
        "estimated_full_minutes": est_min,
    }


class EstimateRequest(BaseModel):
    sample_tables: int = 0        # tables in the scanned sample (for time estimate)
    total_in_schema: int = 0      # full-schema table count (for time estimate)


# ── Step 3: Taxonomy ──────────────────────────────────────────────────────────

class TaxonomyRequest(BaseModel):
    table_names: list[str] = []   # scanned table names — loaded from cache server-side
    sample_tables: int = 0        # tables in the scanned sample (for time estimate)
    total_in_schema: int = 0      # full-schema table count (for time estimate)

@app.post("/api/step/taxonomy")
async def step_taxonomy(req: TaxonomyRequest = TaxonomyRequest()):
    try:
        t0 = _time.monotonic()
        out = await _call(AI_GOVERNANCE_URL, "generate_governance_taxonomy", {
            "table_names": req.table_names or [],
        })
        out = dict(out) if isinstance(out, dict) else {"result": out}
        # Prefer the actual count of tables the tool processed (from the scan cache) —
        # the frontend's sample_tables can be 0 if the scan wasn't run this session.
        processed = ((out.get("_summary") or {}).get("tables_processed")
                     or (out.get("result") or {}).get("_summary", {}).get("tables_processed"))
        sample = processed or req.sample_tables or len(req.table_names or [])
        out.update(_estimate_block(_time.monotonic() - t0, sample, req.total_in_schema))
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 4: Domain Structure ──────────────────────────────────────────────────

@app.post("/api/step/domain_structure/preview")
async def step_domain_structure_preview(req: EstimateRequest = EstimateRequest()):
    try:
        t0 = _time.monotonic()
        out = await _govern("Create the domain structure in CDGC", step="domain_structure")
        out = dict(out) if isinstance(out, dict) else {"result": out}
        # Fixed-cost: writing the domain hierarchy is a one-time op, not per-table.
        out.update(_estimate_block(_time.monotonic() - t0, req.sample_tables,
                                   req.total_in_schema, fixed_cost=True))
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ApproveDomainRequest(BaseModel):
    approved_names: list[str]
    renames: dict[str, str] | None = None


@app.post("/api/step/domain_structure/approve")
async def step_domain_structure_approve(req: ApproveDomainRequest):
    try:
        args: dict = {"approved_names": req.approved_names}
        if req.renames:
            args["renames"] = req.renames
        return await _call(AI_GOVERNANCE_URL, "approve_domain_structure", args)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/domain_structure")
async def step_domain_structure():
    try:
        return await _govern("Create the domain structure in CDGC", step="domain_structure")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 5: Register System & Dataset ────────────────────────────────────────

@app.post("/api/step/system_dataset")
async def step_system_dataset():
    try:
        return await _govern("Register the source system and dataset in CDGC", step="system_dataset")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 6: Curate ────────────────────────────────────────────────────────────

@app.post("/api/step/curate")
async def step_curate(req: EstimateRequest = EstimateRequest()):
    try:
        t0 = _time.monotonic()
        plan = await _govern("Link the columns to their business terms", step="curate")
        plan_error = plan.get("error") or (plan.get("result") or {}).get("error")
        if plan_error:
            raise HTTPException(status_code=400, detail=f"Curate plan failed: {plan_error}")
        batch_count = plan.get("result", {}).get("batch_count", 0)
        batch_size  = plan.get("result", {}).get("batch_size", 40)
        if batch_count == 0:
            raise HTTPException(status_code=400, detail="No columns found to curate. Ensure scan completed successfully.")
        batches: list[dict] = []
        for i in range(batch_count):
            r = await _call(AI_GOVERNANCE_URL, "curate_batch", {
                "batch_index": i, "batch_size": batch_size,
            })
            if r.get("error"):
                raise HTTPException(status_code=400, detail=f"curate_batch[{i}] error: {r['error']}")
            batches.append(r)
            if r.get("done"):
                break
        return {"plan": plan, "batches": batches,
                **_estimate_block(_time.monotonic() - t0, req.sample_tables, req.total_in_schema)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 7: DQ Rules ──────────────────────────────────────────────────────────

@app.post("/api/step/dq_rules")
async def step_dq_rules():
    try:
        plan = await _govern("Create DQ rules for the scanned table", step="dq_rules")
        next_actions = plan.get("result", {}).get("next_actions", [])
        rules: dict | None = None
        for action in next_actions:
            if action.get("tool") == "create_generic_dq_rules":
                p = action["params"]
                call_params = {
                    "table_name":     p["table_name"],
                    "column_ids":     p["column_ids"],
                    "catalog_origin": p["catalog_origin"],
                }
                if p.get("source_table_path"):
                    call_params["source_table_path"] = p["source_table_path"]
                rules = await _call(GOVERNANCE_ENGINE_URL, "create_generic_dq_rules", call_params)
        if rules:
            occurrences = rules.get("occurrences_registered", [])
            if occurrences:
                await _call(AI_GOVERNANCE_URL, "set_dq_occurrences", {"occurrences": occurrences})
        return {"plan": plan, "rules": rules}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 8: Propagate Scores ──────────────────────────────────────────────────

@app.post("/api/step/scores")
async def step_scores():
    try:
        plan = await _govern("Propagate the DQ scores to CDGC", step="propagate_scores")
        next_actions = plan.get("result", {}).get("next_actions", [])
        scores: list[dict] = []
        for action in next_actions:
            if action.get("tool") == "upload_dq_scores":
                p = action["params"]
                r = await _call(GOVERNANCE_ENGINE_URL, "upload_dq_scores", {
                    "asset_id":    p["asset_id"],
                    "value":       p.get("value", 95),
                    "total_count": p.get("total_count", 100),
                    "exception":   p.get("exception", 5),
                })
                r["name"]      = p.get("name", "")
                r["column"]    = p.get("column", "")
                r["dimension"] = p.get("dimension", "")
                scores.append(r)
        return {"plan": plan, "scores": scores, "pushed": len(scores)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 9: MCC Scan ─────────────────────────────────────────────────────────

@app.post("/api/step/mcc_scan")
async def step_mcc_scan():
    try:
        return await _govern("Trigger the MCC Data Quality scan", step="mcc_scan")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PropagateScoreRequest(BaseModel):
    asset_name: str
    score: float
    rule_occurrence_id: str | None = None
    run_date: str | None = None          # ISO date, defaults to today server-side
    dimension: str = "Accuracy"
    passed_rows: int | None = None
    failed_rows: int | None = None
    total_rows: int | None = None


@app.post("/api/step/propagate_dq_score")
async def step_propagate_dq_score(req: PropagateScoreRequest):
    """Single-asset corrective score push (step 10 substep) — the tool for
    fixing or backfilling one DQRO/column without re-running a scan."""
    try:
        args = {k: v for k, v in req.model_dump().items() if v is not None}
        return await _call(AI_GOVERNANCE_URL, "propagate_dq_score", args)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Step 10: Publish to Marketplace ──────────────────────────────────────────

# ── Steps 10–13: Informatica Data Marketplace ────────────────────────────────

@app.post("/api/step/cdmp_category")
async def step_cdmp_category():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_cdmp_category", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/cdmp_data_asset")
async def step_cdmp_data_asset():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_cdmp_data_asset", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/cdmp_collection")
async def step_cdmp_collection():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_cdmp_data_collection", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/publish_marketplace")
async def step_publish_marketplace():
    try:
        return await _call(AI_GOVERNANCE_URL, "publish_cdmp_collection", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/usage_context")
async def step_usage_context():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_cdmp_usage_contexts", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/delivery_template")
async def step_delivery_template():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_delivery_template", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/terms_of_use")
async def step_terms_of_use():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_terms_of_use", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/delivery_target")
async def step_delivery_target():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_delivery_target", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/data_quality")
async def step_data_quality(req: EstimateRequest = EstimateRequest()):
    result = {}
    t0 = _time.monotonic()
    try:
        result["dq_rules"] = await step_dq_rules()
    except Exception as e:
        result["dq_rules"] = {"status": "failed", "error": str(e)}
    try:
        result["scores"] = await step_scores()
    except Exception as e:
        result["scores"] = {"status": "failed", "error": str(e)}
    result.update(_estimate_block(_time.monotonic() - t0, req.sample_tables, req.total_in_schema))
    return result


@app.post("/api/step/create_collection")
async def step_create_collection():
    result = {}
    # link_asset_to_collection must run LAST: it reads the collection_id that
    # create_cdmp_data_collection writes into govern state. Without it, step 12
    # creates a collection, reports success, and links no asset — a silent
    # failure a steward only discovers at step 13, in front of a client.
    for tool, key in [
        ("create_cdmp_category",        "category"),
        ("create_cdmp_data_asset",      "data_asset"),
        ("create_cdmp_data_collection", "collection"),
        ("link_asset_to_collection",    "link"),
    ]:
        try:
            result[key] = await _call(AI_GOVERNANCE_URL, tool, {})
        except Exception as e:
            result[key] = {"status": "failed", "error": str(e)}
    return result


@app.post("/api/step/link_asset_to_collection")
async def step_link_asset_to_collection():
    """Standalone route for step 12's fourth substep button. Run before the
    collection exists, the tool returns its in-band failed status with the
    reason — the substep row renders it red."""
    try:
        return await _call(AI_GOVERNANCE_URL, "link_asset_to_collection", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/publish_marketplace_full")
async def step_publish_marketplace_full():
    result = {}
    for tool, key in [
        ("create_cdmp_category",       "category"),
        ("create_cdmp_data_asset",     "data_asset"),
        ("create_cdmp_data_collection","collection"),
        ("publish_cdmp_collection",    "publish"),
    ]:
        try:
            result[key] = await _call(AI_GOVERNANCE_URL, tool, {})
        except Exception as e:
            result[key] = {"status": "failed", "error": str(e)}
    return result


@app.post("/api/step/configure_delivery")
async def step_configure_delivery():
    result = {}
    for tool, key in [
        ("create_cdmp_usage_contexts", "usage_context"),
        ("create_delivery_template",   "delivery_template"),
        ("create_terms_of_use",        "terms_of_use"),
        ("create_delivery_target",     "delivery_target"),
    ]:
        try:
            result[key] = await _call(AI_GOVERNANCE_URL, tool, {})
        except Exception as e:
            result[key] = {"status": "failed", "error": str(e)}
    return result


@app.post("/api/step/consumer_access")
async def step_consumer_access():
    try:
        return await _call(AI_GOVERNANCE_URL, "create_consumer_access", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/auto_approve_access")
async def step_auto_approve_access():
    try:
        order = await _call(AI_GOVERNANCE_URL, "create_consumer_access", {})
        approve = await _call(AI_GOVERNANCE_URL, "approve_consumer_order", {})
        return {**order, "approved": approve, "auto_approved": True, "status": approve.get("status", order.get("status", "FULFILLED"))}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/approve_order")
async def step_approve_order():
    try:
        return await _call(AI_GOVERNANCE_URL, "approve_consumer_order", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/verify_access")
async def step_verify_access():
    try:
        return await _call(AI_GOVERNANCE_URL, "verify_consumer_access", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/step/withdraw_access")
async def step_withdraw_access():
    try:
        return await _call(AI_GOVERNANCE_URL, "withdraw_consumer_access", {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Static files ──────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")

def main():
    _port = int(os.getenv("GOVERNANCE_UI_PORT", "8080"))
    _host = os.getenv("GOVERNANCE_UI_HOST", "127.0.0.1")
    uvicorn.run("idmc_governance.ui.app:app", host=_host, port=_port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
