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
import re
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

    def _usable(key: str) -> str:
        v = (env.get(key) or "").strip()
        return "" if v.startswith("your_") else v

    template_id = _usable("IDMC_DQ_TEMPLATE_MAPPING_ID")
    return {
        "dmp_collection_id":      env.get("DMP_COLLECTION_ID", ""),
        # Step 9: absence of M_DQ_Generic must surface as setup guidance on
        # step load, not as a run-time failure mid-demo.
        "has_dq_template":        bool(template_id),
        "dq_template_mapping_id": template_id,
        "dq_connection_id":       _usable("IDMC_DQ_CONNECTION_ID"),
        "dq_runtime_env_id":      _usable("IDMC_DQ_RUNTIME_ENV_ID"),
        "dq_schema_path":         env.get("IDMC_DQ_SCHEMA_PATH", ""),
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


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — step 3 Profile Data (governance_engine profiling tools)
# ══════════════════════════════════════════════════════════════════════════════

def _env_value(*keys: str) -> str:
    """First usable value from .env — placeholder values ('your_...') count as unset."""
    env = _read_env_file()
    for k in keys:
        v = (env.get(k) or "").strip()
        if v and not v.startswith("your_"):
            return v
    return ""


class ProfileStepRequest(BaseModel):
    object_name: str
    mode: str = "local"          # local (Snowflake direct) | service (IDMC profiling job)
    database: str | None = None
    schema: str | None = None


@app.post("/api/step/profile")
async def step_profile(req: ProfileStepRequest):
    """Step 3 parent. local → synchronous warehouse stats; service → starts the
    IDMC profiling job and returns ids for the frontend to poll."""
    if req.mode == "local":
        args = {"object_name": req.object_name}
        if req.database:
            args["database"] = req.database
        if req.schema:
            args["schema"] = req.schema
        out = await _bridge(GOVERNANCE_ENGINE_URL, "compute_profile_from_snowflake", args)
        if isinstance(out, dict):
            out["profile_path"] = "snowflake_direct"
        return out
    # service path — asynchronous
    conn = _env_value("IDMC_DQ_CONNECTION_ID")
    rt = _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    if not conn or not rt:
        raise HTTPException(422, "Informatica profiling needs IDMC_DQ_CONNECTION_ID and "
                                 "IDMC_DQ_RUNTIME_ENV_ID in .env — or use 'Compute locally'.")
    args = {"connection_id": conn, "object_name": req.object_name, "runtime_environment_id": rt}
    try:
        out = await _bridge(GOVERNANCE_ENGINE_URL, "run_profile", args)
    except HTTPException as e:
        # No existing profile definition → create one and auto-run it.
        if e.status_code == 500 and "no profile" in str(e.detail).lower():
            out = await _bridge(GOVERNANCE_ENGINE_URL, "create_profile", dict(args, auto_run=True))
        else:
            raise
    if isinstance(out, dict):
        out["profile_path"] = "informatica_service"
    return out


class ProfileSubRequest(BaseModel):
    object_name: str
    database: str | None = None
    schema: str | None = None


@app.post("/api/step/compute_profile_from_snowflake")
async def step_compute_profile(req: ProfileSubRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    out = await _bridge(GOVERNANCE_ENGINE_URL, "compute_profile_from_snowflake", args)
    if isinstance(out, dict):
        out["profile_path"] = "snowflake_direct"
    return out


class ProfileRunRequest(BaseModel):
    object_name: str


@app.post("/api/step/create_profile")
async def step_create_profile(req: ProfileRunRequest):
    conn, rt = _env_value("IDMC_DQ_CONNECTION_ID"), _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    if not conn or not rt:
        raise HTTPException(422, "Set IDMC_DQ_CONNECTION_ID and IDMC_DQ_RUNTIME_ENV_ID in .env.")
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_profile", {
        "connection_id": conn, "object_name": req.object_name,
        "runtime_environment_id": rt, "auto_run": True})


@app.post("/api/step/run_profile")
async def step_run_profile(req: ProfileRunRequest):
    conn, rt = _env_value("IDMC_DQ_CONNECTION_ID"), _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    if not conn or not rt:
        raise HTTPException(422, "Set IDMC_DQ_CONNECTION_ID and IDMC_DQ_RUNTIME_ENV_ID in .env.")
    return await _bridge(GOVERNANCE_ENGINE_URL, "run_profile", {
        "connection_id": conn, "object_name": req.object_name, "runtime_environment_id": rt})


@app.post("/api/step/get_profile_results")
async def step_get_profile_results(req: ProfileRunRequest):
    out = await _bridge(GOVERNANCE_ENGINE_URL, "get_profile_results", {"object_name": req.object_name})
    if isinstance(out, dict):
        out["profile_path"] = "cdgc_catalog"
    return out


class ProfileJobStatusRequest(BaseModel):
    job_id: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None


@app.post("/api/profile/job_status")
async def profile_job_status(req: ProfileJobStatusRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    if not args:
        raise HTTPException(422, "Pass at least one of job_id / profile_id / profile_name.")
    return await _bridge(GOVERNANCE_ENGINE_URL, "get_profile_results_direct", args)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — step 7 Recommend Rules: pure reasoning over the step 3 profile,
# read from the session store — never calls the profiling tools directly.
# ══════════════════════════════════════════════════════════════════════════════

class RecommendRulesRequest(BaseModel):
    connection: str = ""
    schema: str = ""
    table: str
    rule_name_prefix: str = "DQ"


@app.post("/api/step/recommend_rules")
async def step_recommend_rules(req: RecommendRulesRequest):
    entry = _read_profile_state().get(_profile_key(req.connection, req.schema, req.table))
    profile = (entry or {}).get("profile") or {}
    if not profile.get("columns"):
        raise HTTPException(422, "No profile found for this table — run Profile Data (step 3) first. "
                                 "recommend_dq_rules is pure reasoning over a profile payload and "
                                 "produces nothing useful from an empty one.")
    return await _bridge(GOVERNANCE_ENGINE_URL, "recommend_dq_rules", {
        "profile_results":  {"total_rows": profile.get("total_rows", 0), "columns": profile.get("columns", {})},
        "rule_name_prefix": req.rule_name_prefix,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Phase 6 — step 7 rule library substeps
# ══════════════════════════════════════════════════════════════════════════════

class RuleSpecsRequest(BaseModel):
    top: int = 100
    name_filter: str | None = None


@app.post("/api/step/list_rule_specifications")
async def step_list_rule_specifications(req: RuleSpecsRequest = RuleSpecsRequest()):
    """Org rule specs + the seven local templates in examples/, labelled apart —
    the templates prove the 'DQ rule template library' claim."""
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    out = await _bridge(GOVERNANCE_ENGINE_URL, "list_rule_specifications", args)
    templates = []
    for f in sorted((_REPO_ROOT / "examples").glob("*.json")):
        if f.name == "profiling-rule-mapping.json":   # recommender config, not a template
            continue
        try:
            j = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        opts = {o.get("name"): o.get("optionValue") for o in j.get("options", [])}
        templates.append({
            "file":      f"examples/{f.name}",
            "template":  f.stem,
            "dimension": opts.get("DIMENSION"),
            "fields":    [fl.get("name") for fl in j.get("fields", [])],
            "source":    "local_template",
        })
    if isinstance(out, dict):
        out["local_templates"] = templates
    return out


class CreateRuleRequest(BaseModel):
    rule_name: str
    description: str = ""
    field_name: str = "Input"
    dimension: str = "COMPLETENESS"
    rule_template: str | None = None    # e.g. "examples/range-check.json"


@app.post("/api/step/create_dq_rules")
async def step_create_dq_rules(req: CreateRuleRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    if not args.get("description"):
        args["description"] = f"Authored at the step 7 review gate ({req.dimension.lower()})"
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_dq_rules", args)


class ValidateRuleRequest(BaseModel):
    rule_template: str | None = None
    field_name: str = "Input"
    dimension: str = "COMPLETENESS"


@app.post("/api/step/validate_rule")
async def step_validate_rule(req: ValidateRuleRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "validate_rule", args)


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — step 9 Schedule Execution (governance_engine CDI tools)
# ══════════════════════════════════════════════════════════════════════════════

class ListTasksRequest(BaseModel):
    top: int = 100
    name_filter: str | None = None


@app.post("/api/step/schedule_execution")
async def step_schedule_execution(req: ListTasksRequest = ListTasksRequest()):
    """Step 9 parent = list current mapping tasks (read-only; populates pickers)."""
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "list_mapping_tasks", args)


@app.post("/api/step/list_mapping_tasks")
async def step_list_mapping_tasks(req: ListTasksRequest = ListTasksRequest()):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "list_mapping_tasks", args)


class CreateTaskRequest(BaseModel):
    name: str
    mapping_id: str
    runtime_environment_id: str = ""
    description: str = ""


@app.post("/api/step/create_mapping_task")
async def step_create_mapping_task(req: CreateTaskRequest):
    rt = req.runtime_environment_id or _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    if not rt:
        raise HTTPException(422, "runtime_environment_id required (or set IDMC_DQ_RUNTIME_ENV_ID in .env).")
    args = req.model_dump()
    args["runtime_environment_id"] = rt
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_mapping_task", args)


class GenerateDQTaskRequest(BaseModel):
    source_table: str
    target_table: str = ""
    rule_spec_id: str | None = None
    task_name: str | None = None
    input_field_mapping: str = ""


@app.post("/api/step/generate_dq_mapping_task")
async def step_generate_dq_task(req: GenerateDQTaskRequest):
    conn = _env_value("IDMC_DQ_CONNECTION_ID")
    rt   = _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    tmpl = _env_value("IDMC_DQ_TEMPLATE_MAPPING_ID")
    if not tmpl:
        # Missing M_DQ_Generic surfaces as setup guidance on load, not a run-time failure.
        raise HTTPException(422, "M_DQ_Generic is not configured (IDMC_DQ_TEMPLATE_MAPPING_ID). "
                                 "Build it per docs/IDMC_Console_Runbook.md, then set its id in .env.")
    if not conn or not rt:
        raise HTTPException(422, "Set IDMC_DQ_CONNECTION_ID and IDMC_DQ_RUNTIME_ENV_ID in .env.")
    args = {
        "source_connection_id":   conn,
        "source_table":           req.source_table,
        "target_connection_id":   conn,
        "target_table":           req.target_table or f"{req.source_table}_BAD_RECORDS",
        "runtime_environment_id": rt,
        "template_mapping_id":    tmpl,
        "input_field_mapping":    req.input_field_mapping,
    }
    if req.rule_spec_id:
        args["rule_spec_id"] = req.rule_spec_id
    if req.task_name:
        args["task_name"] = req.task_name
    return await _bridge(GOVERNANCE_ENGINE_URL, "generate_dq_mapping_task", args)


# Malformed startTime is accepted by the schedule API and fails silently later —
# validate and normalise to .000Z here.
_START_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d{1,3}))?(?:Z)?$")
_SCHEDULE_INTERVALS = {"None", "Minutely", "Hourly", "Daily", "Weekly", "Biweekly", "Monthly"}


def _normalize_start_time(ts: str) -> str:
    m = _START_TIME_RE.match((ts or "").strip())
    if not m:
        raise HTTPException(422, f"Invalid startTime '{ts}'. Use ISO-8601 UTC like 2026-07-24T09:00:00.000Z.")
    date, hh, mm, ss, ms = m.groups()
    return f"{date}T{hh}:{mm}:{ss or '00'}.{(ms or '0'):0<3}Z"


class ScheduleCreateRequest(BaseModel):
    name: str
    start_time: str
    interval: str = "Daily"
    frequency: int | None = None
    sun: bool = False
    mon: bool = False
    tue: bool = False
    wed: bool = False
    thu: bool = False
    fri: bool = False
    sat: bool = False
    day_of_month: int | None = None


@app.post("/api/step/create_schedule")
async def step_create_schedule(req: ScheduleCreateRequest):
    if req.interval not in _SCHEDULE_INTERVALS:
        raise HTTPException(422, f"interval must be one of {sorted(_SCHEDULE_INTERVALS)}.")
    start = _normalize_start_time(req.start_time)
    args = {k: v for k, v in req.model_dump().items() if v is not None and v is not False}
    args["start_time"] = start
    args["start_time_utc"] = start
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_schedule", args)


class TaskflowCreateRequest(BaseModel):
    name: str
    tasks: list[dict]            # [{taskId, type, name}]
    description: str = ""


@app.post("/api/step/create_linear_taskflow")
async def step_create_taskflow(req: TaskflowCreateRequest):
    if not req.tasks:
        raise HTTPException(422, "A taskflow needs at least one task.")
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_linear_taskflow", req.model_dump())


class RunTaskRequest(BaseModel):
    task_id: str
    task_type: str = "MTT"


@app.post("/api/step/run_task")
async def step_run_task(req: RunTaskRequest):
    return await _bridge(GOVERNANCE_ENGINE_URL, "run_task", req.model_dump())


class JobStatusRequest(BaseModel):
    run_id: int
    task_id: str | None = None


@app.post("/api/step/get_job_status")
async def step_get_job_status(req: JobStatusRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "get_job_status", args)


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
    # Step 3 profile identity — classification grounds in data, not column names.
    connection: str = ""
    schema: str = ""
    table: str = ""


def _compact_profile_stats(profile: dict) -> dict:
    """Boil the step 3 profile down to per-column stats the LLM prompt can carry."""
    out = {}
    for name, st in list((profile.get("columns") or {}).items())[:40]:
        if not isinstance(st, dict):
            continue
        row = {}
        for k_src, k_dst in (("data_type", "type"), ("null_pct", "null_pct"),
                             ("null_count", "nulls"), ("distinct_count", "distinct"),
                             ("min_value", "min"), ("max_value", "max")):
            if st.get(k_src) is not None:
                row[k_dst] = st[k_src]
        tv = (st.get("top_values") or [{}])[0]
        if isinstance(tv, dict) and tv.get("value") is not None:
            row["top"] = f"{str(tv['value'])[:24]} x{tv.get('count')}"
        out[name] = row
    return {"total_rows": profile.get("total_rows"), "columns": out}


@app.post("/api/step/taxonomy")
async def step_taxonomy(req: TaxonomyRequest = TaxonomyRequest()):
    try:
        t0 = _time.monotonic()
        args: dict = {"table_names": req.table_names or []}
        # Phase 1: send the step 3 profile with the column metadata so the LLM
        # classifies on what the data contains, not what the column is called —
        # the exact thing the Opella customer rejected.
        if req.table:
            entry = _read_profile_state().get(_profile_key(req.connection, req.schema, req.table))
            profile = (entry or {}).get("profile") or {}
            if profile.get("columns"):
                args["profile_stats"] = _compact_profile_stats(profile)
        out = await _call(AI_GOVERNANCE_URL, "generate_governance_taxonomy", args)
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
        # Gate 2: batches run in DRY-RUN — the LLM proposes links, nothing is
        # written. The gate's approve action commits the selected subset via
        # apply_curation_links. Deselected columns stay unlinked.
        batches: list[dict] = []
        matches: list[dict] = []
        for i in range(batch_count):
            r = await _call(AI_GOVERNANCE_URL, "curate_batch", {
                "batch_index": i, "batch_size": batch_size, "dry_run": True,
            })
            if r.get("error"):
                raise HTTPException(status_code=400, detail=f"curate_batch[{i}] error: {r['error']}")
            batches.append(r)
            matches.extend(r.get("matches") or [])
            if r.get("done"):
                break
        return {"plan": plan, "batches": batches, "matches": matches,
                "awaiting_approval": True,
                **_estimate_block(_time.monotonic() - t0, req.sample_tables, req.total_in_schema)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ApplyCurationRequest(BaseModel):
    links: list[dict]


@app.post("/api/step/apply_curation_links")
async def step_apply_curation_links(req: ApplyCurationRequest):
    """Gate 2 commit — writes exactly the approved column-to-term links."""
    if not req.links:
        raise HTTPException(422, "Nothing approved — select at least one link.")
    return await _bridge(AI_GOVERNANCE_URL, "apply_curation_links", {"links": req.links})


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


# ══════════════════════════════════════════════════════════════════════════════
# Phase 6 remainder — step 1 substeps + settings-panel export/import
# ══════════════════════════════════════════════════════════════════════════════

class ScanSourceRequest(BaseModel):
    table_names: list[str]
    schema_hint: str | None = None
    force_refresh: bool = False


@app.post("/api/step/scan_mcc_source")
async def step_scan_mcc_source(req: ScanSourceRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(AI_GOVERNANCE_URL, "scan_mcc_source", args)


class ConnectionsRequest(BaseModel):
    top: int = 100
    type_filter: str | None = None


@app.post("/api/step/list_connections")
async def step_list_connections(req: ConnectionsRequest = ConnectionsRequest()):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "list_connections", args)


# Settings panel: export/import act on the ORG, not the dataset in flight —
# they live behind the gear icon, never on the ladder.
_EXPORT_DIR = _REPO_ROOT / "state" / "exports"


class ExportRequest(BaseModel):
    object_ids: list[str]
    name: str | None = None
    include_dependencies: bool = True


@app.post("/api/settings/export")
async def settings_export(req: ExportRequest):
    if not req.object_ids:
        raise HTTPException(422, "Provide at least one object id to export.")
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    args = {"object_ids": req.object_ids, "output_dir": str(_EXPORT_DIR),
            "include_dependencies": req.include_dependencies}
    if req.name:
        args["name"] = req.name
    out = await _bridge(GOVERNANCE_ENGINE_URL, "export_assets", args)
    if isinstance(out, dict) and out.get("package_path"):
        out["download_url"] = f"/api/settings/export/download/{Path(out['package_path']).name}"
    return out


@app.get("/api/settings/export/download/{filename}")
async def settings_export_download(filename: str):
    safe = Path(filename).name          # basename only — no traversal
    p = _EXPORT_DIR / safe
    if not p.exists():
        raise HTTPException(404, "Export package not found (it may have been cleaned up).")
    return FileResponse(str(p), media_type="application/zip", filename=safe)


class ImportRequest(BaseModel):
    filename: str
    content_base64: str
    default_conflict: str = "REUSE"


@app.post("/api/settings/import")
async def settings_import(req: ImportRequest):
    import base64
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    dest = _EXPORT_DIR / Path(req.filename or "import.zip").name
    try:
        dest.write_bytes(base64.b64decode(req.content_base64))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Could not decode uploaded package: {e}")
    return await _bridge(GOVERNANCE_ENGINE_URL, "import_package", {
        "zip_path": str(dest), "default_conflict": req.default_conflict,
    })


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
