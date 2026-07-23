"""governance_ui.py — Branded wizard UI for the IDMC governance pipeline.

Serves a React frontend on http://127.0.0.1:8080 and bridges REST calls
to the six MCP servers:
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


# An overnight-expired IDMC session surfaces as 401/403 with REPO_38205 buried in a
# tool error string. Map it to HTTP 401 so ONE frontend interceptor can offer
# "session expired — reconnect" instead of every screen showing a raw 500.
_SESSION_EXPIRY_MARKERS = ("repo_38205", "http 401", "unauthorized", "session expired",
                           "invalid session", "session is not valid")


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

async def _probe_server(name: str, url: str) -> dict:
    async def _ping():
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

    try:
        await asyncio.wait_for(_ping(), timeout=4)
        return {"server": name, "url": url, "ok": True}
    except (Exception, BaseExceptionGroup) as e:  # anyio wraps failures in groups
        root = _unwrap_exception(e)
        return {"server": name, "url": url, "ok": False,
                "error": (str(root) or type(root).__name__)[:200]}


@app.get("/api/health")
async def health():
    results = await asyncio.gather(*[_probe_server(n, u) for n, u in MCP_SERVERS.items()])
    down = [r["server"] for r in results if not r["ok"]]
    return {"servers": list(results), "down": down, "all_ok": not down,
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
        "dq_connection_id":       _usable("IDMC_DQ_CONNECTION_ID"),
        "dq_runtime_env_id":      _usable("IDMC_DQ_RUNTIME_ENV_ID"),
        "dq_template_mapping_id": template_id,
        # Operate/Mapping Tasks: absence of M_DQ_Generic must surface as setup
        # guidance on screen load, not as a run-time failure mid-demo.
        "has_dq_template":        bool(template_id),
        "dq_schema_path":         env.get("IDMC_DQ_SCHEMA_PATH", ""),
        "governance_system_name": env.get("GOVERNANCE_SYSTEM_NAME", ""),
    }


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
    for tool, key in [
        ("create_cdmp_category",        "category"),
        ("create_cdmp_data_asset",      "data_asset"),
        ("create_cdmp_data_collection", "collection"),
    ]:
        try:
            result[key] = await _call(AI_GOVERNANCE_URL, tool, {})
        except Exception as e:
            result[key] = {"status": "failed", "error": str(e)}
    return result


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


# ══════════════════════════════════════════════════════════════════════════════
# Full-coverage sections: Profile (Govern 6-7), Operate, Monitor, Explore,
# Glossary, Admin. Each route is a thin bridge to one MCP tool; shared error
# translation lives in _bridge/_as_http_error.
# ══════════════════════════════════════════════════════════════════════════════

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXPORT_DIR = _REPO_ROOT / "state" / "exports"


def _env_value(*keys: str) -> str:
    """First usable value from .env — placeholder values ('your_...') count as unset."""
    env = _read_env_file()
    for k in keys:
        v = (env.get(k) or "").strip()
        if v and not v.startswith("your_"):
            return v
    return ""


# ── Phase 1: Profiling & rule recommendation ─────────────────────────────────

class ProfileComputeRequest(BaseModel):
    object_name: str
    database: str | None = None
    schema: str | None = None
    columns: list[str] | None = None
    top_n_values: int = 10


@app.post("/api/profile/compute_local")
async def profile_compute_local(req: ProfileComputeRequest):
    """Compute column statistics directly against Snowflake (synchronous)."""
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    out = await _bridge(GOVERNANCE_ENGINE_URL, "compute_profile_from_snowflake", args)
    if isinstance(out, dict):
        out["profile_path"] = "snowflake_direct"
    return out


class ProfileRunRequest(BaseModel):
    object_name: str
    connection_id: str = ""
    runtime_environment_id: str = ""
    auto_create: bool = False          # create the profile definition if none exists
    columns: list[dict] | None = None  # [{name,dataType,precision,scale}] for auto_create


@app.post("/api/profile/execute")
async def profile_execute(req: ProfileRunRequest):
    """Run the Informatica profiling service (asynchronous — poll job_status)."""
    conn = req.connection_id or _env_value("IDMC_DQ_CONNECTION_ID")
    rt   = req.runtime_environment_id or _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    if not conn or not rt:
        raise HTTPException(422, "No connection/runtime configured. Set IDMC_DQ_CONNECTION_ID "
                                 "and IDMC_DQ_RUNTIME_ENV_ID in .env, or pass them explicitly.")
    args = {"connection_id": conn, "object_name": req.object_name,
            "runtime_environment_id": rt}
    try:
        out = await _bridge(GOVERNANCE_ENGINE_URL, "run_profile", args)
    except HTTPException as e:
        # No existing profile definition → optionally create one and auto-run it.
        if req.auto_create and e.status_code == 500 and "no profile" in str(e.detail).lower():
            create_args = dict(args, auto_run=True)
            if req.columns:
                create_args["columns"] = req.columns
            out = await _bridge(GOVERNANCE_ENGINE_URL, "create_profile", create_args)
        else:
            raise
    if isinstance(out, dict):
        out["profile_path"] = "informatica_service"
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


class ProfileResultsRequest(BaseModel):
    object_name: str


@app.post("/api/profile/results")
async def profile_results(req: ProfileResultsRequest):
    """Column statistics from CDGC (populated a few minutes after a profile run)."""
    out = await _bridge(GOVERNANCE_ENGINE_URL, "get_profile_results", {"object_name": req.object_name})
    if isinstance(out, dict):
        out["profile_path"] = "cdgc_catalog"
    return out


class RecommendRulesRequest(BaseModel):
    profile_results: dict
    rule_name_prefix: str = "DQ"


@app.post("/api/profile/recommend")
async def profile_recommend(req: RecommendRulesRequest):
    """Pure reasoning over profile output — requires a completed Profile step."""
    if not req.profile_results or not req.profile_results.get("columns"):
        raise HTTPException(422, "No profile statistics supplied — run the Profile Data step first.")
    return await _bridge(GOVERNANCE_ENGINE_URL, "recommend_dq_rules", {
        "profile_results":  req.profile_results,
        "rule_name_prefix": req.rule_name_prefix,
    })


class ProfileUnattendedRequest(BaseModel):
    object_name: str
    dry_run: bool = True
    auto_create_rules: bool = False
    run_now: bool = False
    use_snowflake_direct: bool = True
    rule_name_prefix: str = "DQ"


@app.post("/api/profile/unattended")
async def profile_unattended(req: ProfileUnattendedRequest):
    """profile_and_govern — unattended mode for Govern steps 6-9."""
    return await _bridge(GOVERNANCE_ENGINE_URL, "profile_and_govern", req.model_dump())


class GovernUnattendedRequest(BaseModel):
    table_names: list[str]
    schema_hint: str | None = None
    domain_hint: str | None = None
    organization_context: str | None = None
    dry_run: bool = False
    skip_steps: list[str] | None = None


@app.post("/api/govern/unattended")
async def govern_unattended(req: GovernUnattendedRequest):
    """onboard_and_govern — headless scan→taxonomy→domains→system→curate."""
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(AI_GOVERNANCE_URL, "onboard_and_govern", args)


# ── Phase 2: Operate ──────────────────────────────────────────────────────────

class ListTasksRequest(BaseModel):
    top: int = 50
    name_filter: str | None = None


@app.post("/api/operate/tasks/list")
async def operate_list_tasks(req: ListTasksRequest = ListTasksRequest()):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "list_mapping_tasks", args)


class CreateTaskRequest(BaseModel):
    name: str
    mapping_id: str
    runtime_environment_id: str = ""
    description: str = ""
    schedule_id: str | None = None
    mapping_parameters: dict | None = None


@app.post("/api/operate/tasks/create")
async def operate_create_task(req: CreateTaskRequest):
    rt = req.runtime_environment_id or _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    if not rt:
        raise HTTPException(422, "runtime_environment_id required (or set IDMC_DQ_RUNTIME_ENV_ID in .env).")
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    args["runtime_environment_id"] = rt
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_mapping_task", args)


class GenerateDQTaskRequest(BaseModel):
    source_table: str
    source_connection_id: str = ""
    target_connection_id: str = ""
    target_table: str = ""
    runtime_environment_id: str = ""
    rule_spec_id: str | None = None
    task_name: str | None = None
    input_field_mapping: str = ""
    template_mapping_id: str = ""
    description: str = ""


@app.post("/api/operate/tasks/generate_dq")
async def operate_generate_dq_task(req: GenerateDQTaskRequest):
    conn = req.source_connection_id or _env_value("IDMC_DQ_CONNECTION_ID")
    rt   = req.runtime_environment_id or _env_value("IDMC_DQ_RUNTIME_ENV_ID")
    tmpl = req.template_mapping_id or _env_value("IDMC_DQ_TEMPLATE_MAPPING_ID")
    if not tmpl:
        # Detect the missing M_DQ_Generic template up front — setup guidance, not a run-time failure.
        raise HTTPException(422, "M_DQ_Generic template mapping is not configured "
                                 "(IDMC_DQ_TEMPLATE_MAPPING_ID). Import the M_DQ_Generic mapping into "
                                 "this org and set its id in .env before generating DQ tasks.")
    if not conn or not rt:
        raise HTTPException(422, "Missing connection/runtime. Set IDMC_DQ_CONNECTION_ID and "
                                 "IDMC_DQ_RUNTIME_ENV_ID in .env, or pass them explicitly.")
    args = {
        "source_connection_id":   conn,
        "source_table":           req.source_table,
        "target_connection_id":   req.target_connection_id or conn,
        "target_table":           req.target_table or req.source_table,
        "runtime_environment_id": rt,
        "template_mapping_id":    tmpl,
        "input_field_mapping":    req.input_field_mapping,
        "description":            req.description,
    }
    if req.rule_spec_id:
        args["rule_spec_id"] = req.rule_spec_id
    if req.task_name:
        args["task_name"] = req.task_name
    return await _bridge(GOVERNANCE_ENGINE_URL, "generate_dq_mapping_task", args)


# Malformed startTime is accepted by the schedule API and fails silently later —
# validate here, per the v2 contract (.000Z).
_START_TIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d{1,3}))?(?:Z)?$")
_SCHEDULE_INTERVALS = {"None", "Minutely", "Hourly", "Daily", "Weekly", "Biweekly", "Monthly"}


def _normalize_start_time(ts: str) -> str:
    m = _START_TIME_RE.match((ts or "").strip())
    if not m:
        raise HTTPException(422, f"Invalid startTime '{ts}'. Use an ISO-8601 UTC timestamp "
                                 "like 2026-07-23T09:00:00.000Z.")
    date, hh, mm, ss, ms = m.groups()
    return f"{date}T{hh}:{mm}:{ss or '00'}.{(ms or '0'):0<3}Z"


class ScheduleCreateRequest(BaseModel):
    name: str
    start_time: str
    start_time_utc: str | None = None
    interval: str = "Daily"
    frequency: int | None = None
    description: str = ""
    end_time: str | None = None
    sun: bool = False
    mon: bool = False
    tue: bool = False
    wed: bool = False
    thu: bool = False
    fri: bool = False
    sat: bool = False
    week_day: bool = False
    day_of_month: int | None = None
    week_of_month: str | None = None
    day_of_week: str | None = None


@app.post("/api/operate/schedules/create")
async def operate_create_schedule(req: ScheduleCreateRequest):
    if req.interval not in _SCHEDULE_INTERVALS:
        raise HTTPException(422, f"interval must be one of {sorted(_SCHEDULE_INTERVALS)}.")
    start = _normalize_start_time(req.start_time)
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    args["start_time"] = start
    args["start_time_utc"] = _normalize_start_time(req.start_time_utc) if req.start_time_utc else start
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_schedule", args)


class TaskflowCreateRequest(BaseModel):
    name: str
    tasks: list[dict]           # [{taskId, type, name}]
    description: str = ""
    schedule_id: str | None = None


@app.post("/api/operate/taskflows/create")
async def operate_create_taskflow(req: TaskflowCreateRequest):
    if not req.tasks:
        raise HTTPException(422, "A taskflow needs at least one task.")
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_linear_taskflow", args)


class RunTaskRequest(BaseModel):
    task_id: str
    task_type: str = "MTT"


@app.post("/api/operate/run")
async def operate_run_task(req: RunTaskRequest):
    return await _bridge(GOVERNANCE_ENGINE_URL, "run_task", req.model_dump())


class JobStatusRequest(BaseModel):
    run_id: int
    task_id: str | None = None


@app.post("/api/operate/job_status")
async def operate_job_status(req: JobStatusRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "get_job_status", args)


class PipelineRequest(BaseModel):
    rule_name: str
    runtime_environment_id: str = ""
    goal: str = ""
    rule_description: str = "Created from the Operate console"
    rule_dimension: str = "COMPLETENESS"
    rule_template: str | None = None
    source_connection_id: str | None = None
    source_table: str | None = None
    target_connection_id: str | None = None
    target_table: str | None = None
    input_field_mapping: str = ""
    task_name: str | None = None
    schedule_name: str | None = None
    schedule_start_time: str | None = None
    schedule_start_time_utc: str | None = None
    schedule_interval: str = "Daily"
    schedule_frequency: int | None = None
    cdgc_column_id: str | None = None
    cdgc_occurrence_name: str | None = None
    cdgc_dimension: str | None = None
    cdgc_catalog_origin: str | None = None
    run_now: bool = False
    score_value: float | None = None
    score_total_count: int | None = None
    score_exception: int | None = None
    score_asset_id: str | None = None


@app.post("/api/operate/pipeline")
async def operate_pipeline(req: PipelineRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    args["runtime_environment_id"] = (req.runtime_environment_id
                                      or _env_value("IDMC_DQ_RUNTIME_ENV_ID"))
    if not args["runtime_environment_id"]:
        raise HTTPException(422, "runtime_environment_id required (or set IDMC_DQ_RUNTIME_ENV_ID in .env).")
    if not args.get("source_connection_id"):
        conn = _env_value("IDMC_DQ_CONNECTION_ID")
        if conn:
            args["source_connection_id"] = conn
    if args.get("schedule_start_time"):
        args["schedule_start_time"] = _normalize_start_time(args["schedule_start_time"])
        args.setdefault("schedule_start_time_utc", args["schedule_start_time"])
    return await _bridge(GOVERNANCE_ENGINE_URL, "run_governance_pipeline", args)


# ── Phase 3: Monitor ──────────────────────────────────────────────────────────

class ScoresRequest(BaseModel):
    asset_name: str
    dimension: str | None = None


@app.post("/api/monitor/scores")
async def monitor_scores(req: ScoresRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(DQ_MONITOR_URL, "get_dq_scores", args)


class TrendsRequest(BaseModel):
    asset_name: str
    lookback_days: int = 30
    degradation_delta: float = 10.0


@app.post("/api/monitor/trends")
async def monitor_trends(req: TrendsRequest):
    return await _bridge(DQ_MONITOR_URL, "check_score_trends", req.model_dump())


class RemediationRequest(BaseModel):
    asset_name: str


@app.post("/api/monitor/remediation")
async def monitor_remediation(req: RemediationRequest):
    return await _bridge(DQ_MONITOR_URL, "recommend_remediation", req.model_dump())


class AlertCreateRequest(BaseModel):
    asset_name: str
    threshold: float
    notify_email: str
    dimension: str | None = None
    lookback_days: int = 30
    note: str = ""


@app.post("/api/monitor/alerts/create")
async def monitor_create_alert(req: AlertCreateRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(DQ_MONITOR_URL, "alert_on_degradation", args)


@app.get("/api/monitor/alerts")
async def monitor_list_alerts():
    """Configured alert rules — stored by OUR platform (.dq_monitor_alerts.json),
    evaluated by our scheduler. CDGC has no alert-registration API."""
    # Local runs write to the repo root; docker-compose redirects dq-monitor's
    # root to the shared ./state mount — check both.
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


# ── Phase 4: Explore ──────────────────────────────────────────────────────────

class LineageRequest(BaseModel):
    asset_name: str
    direction: str = "all"      # upstream | downstream | all
    depth: int = 3
    level: str = "dataset"


@app.post("/api/explore/lineage")
async def explore_lineage(req: LineageRequest):
    args = req.model_dump()
    args["depth"] = max(1, min(args["depth"], 5))   # deep traversals = many sequential calls
    return await _bridge(LINEAGE_REPORTER_URL, "trace_lineage", args)


class ImpactRequest(BaseModel):
    asset_name: str
    change_description: str = ""
    depth: int = 3
    level: str = "dataset"


@app.post("/api/explore/impact")
async def explore_impact(req: ImpactRequest):
    args = req.model_dump()
    args["depth"] = max(1, min(args["depth"], 5))
    args["change_description"] = args["change_description"] or "Proposed change (unspecified)"
    return await _bridge(LINEAGE_REPORTER_URL, "generate_impact_report", args)


class SourceFinderRequest(BaseModel):
    asset_name: str
    depth: int = 3
    level: str = "dataset"


@app.post("/api/explore/source")
async def explore_source(req: SourceFinderRequest):
    args = req.model_dump()
    args["depth"] = max(1, min(args["depth"], 5))
    return await _bridge(LINEAGE_REPORTER_URL, "find_data_source", args)


# ── Phase 5: Glossary ─────────────────────────────────────────────────────────

class SuggestTermsRequest(BaseModel):
    asset_name: str
    domain_context: str | None = None


@app.post("/api/glossary/suggest")
async def glossary_suggest(req: SuggestTermsRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GLOSSARY_MANAGER_URL, "suggest_terms_for_asset", args)


class CreateTermRequest(BaseModel):
    term_name: str
    definition: str
    category: str | None = None
    synonyms: list[str] = []


@app.post("/api/glossary/term/create")
async def glossary_create_term(req: CreateTermRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GLOSSARY_MANAGER_URL, "create_glossary_term", args)


class GlossaryHealthRequest(BaseModel):
    scan_scope: str = "all"
    sample_size: int = 200
    min_definition_length: int = 20


@app.post("/api/glossary/health")
async def glossary_health(req: GlossaryHealthRequest = GlossaryHealthRequest()):
    return await _bridge(GLOSSARY_MANAGER_URL, "detect_glossary_issues", req.model_dump())


# ── Phase 6: Admin ────────────────────────────────────────────────────────────

class ConnectionsRequest(BaseModel):
    top: int = 50
    type_filter: str | None = None


@app.post("/api/admin/connections")
async def admin_connections(req: ConnectionsRequest = ConnectionsRequest()):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "list_connections", args)


class RuleSpecsRequest(BaseModel):
    top: int = 50
    name_filter: str | None = None


@app.post("/api/admin/rules/list")
async def admin_list_rules(req: RuleSpecsRequest = RuleSpecsRequest()):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "list_rule_specifications", args)


@app.get("/api/admin/rule_templates")
async def admin_rule_templates():
    """The local template library (examples/*.json) — rendered alongside org rules,
    labelled as local templates."""
    ex_dir = _REPO_ROOT / "examples"
    out = []
    for f in sorted(ex_dir.glob("*.json")):
        if f.name == "profiling-rule-mapping.json":  # recommender config, not a rule template
            continue
        try:
            j = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        opts = {o.get("name"): o.get("optionValue") for o in j.get("options", [])}
        knobs = {k: v for k, v in opts.items()
                 if k in ("MIN_VALUE", "MAX_VALUE", "PATTERN", "MAX_AGE_DAYS", "MAX_OCCURRENCES")}
        out.append({
            "file":       f.name,
            "template":   f.stem,
            "dimension":  opts.get("DIMENSION"),
            "fields":     [fl.get("name") for fl in j.get("fields", [])],
            "options":    knobs,
            "description": ((j.get("alternateDefinition") or {}).get("script") or "")[:240],
            "source":     "local_template",
        })
    return {"templates": out, "count": len(out)}


class CreateRuleRequest(BaseModel):
    rule_name: str
    description: str = ""
    field_name: str = "Input"
    dimension: str = "COMPLETENESS"
    rule_template: str | None = None    # e.g. "examples/range-check.json"


@app.post("/api/admin/rules/create")
async def admin_create_rule(req: CreateRuleRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    if not args.get("description"):
        args["description"] = f"Authored in the Admin rule library ({req.dimension.lower()})"
    return await _bridge(GOVERNANCE_ENGINE_URL, "create_dq_rules", args)


class ValidateRuleRequest(BaseModel):
    rule_template: str | None = None
    rule_model: dict | None = None
    field_name: str = "Input"
    dimension: str = "COMPLETENESS"


@app.post("/api/admin/rules/validate")
async def admin_validate_rule(req: ValidateRuleRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(GOVERNANCE_ENGINE_URL, "validate_rule", args)


class ExportRequest(BaseModel):
    object_ids: list[str]
    name: str | None = None
    include_dependencies: bool = True


@app.post("/api/admin/export")
async def admin_export(req: ExportRequest):
    if not req.object_ids:
        raise HTTPException(422, "Select at least one object id to export.")
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    args = {"object_ids": req.object_ids, "output_dir": str(_EXPORT_DIR),
            "include_dependencies": req.include_dependencies}
    if req.name:
        args["name"] = req.name
    out = await _bridge(GOVERNANCE_ENGINE_URL, "export_assets", args)
    if isinstance(out, dict) and out.get("package_path"):
        out["download_url"] = f"/api/admin/export/download/{Path(out['package_path']).name}"
    return out


@app.get("/api/admin/export/download/{filename}")
async def admin_export_download(filename: str):
    safe = Path(filename).name          # basename only — no traversal
    p = _EXPORT_DIR / safe
    if not p.exists():
        raise HTTPException(404, "Export package not found (it may have been cleaned up).")
    return FileResponse(str(p), media_type="application/zip", filename=safe)


class ImportRequest(BaseModel):
    filename: str
    content_base64: str
    default_conflict: str = "REUSE"


@app.post("/api/admin/import")
async def admin_import(req: ImportRequest):
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


class ScanSourceRequest(BaseModel):
    table_names: list[str]
    schema_hint: str | None = None
    force_refresh: bool = False


@app.post("/api/admin/scan_source")
async def admin_scan_source(req: ScanSourceRequest):
    args = {k: v for k, v in req.model_dump().items() if v is not None}
    return await _bridge(AI_GOVERNANCE_URL, "scan_mcc_source", args)


# ── Marketplace addition ──────────────────────────────────────────────────────

@app.post("/api/step/link_asset_to_collection")
async def step_link_asset_to_collection():
    return await _bridge(AI_GOVERNANCE_URL, "link_asset_to_collection", {})


# ── Static files ──────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")

def main():
    _port = int(os.getenv("GOVERNANCE_UI_PORT", "8080"))
    _host = os.getenv("GOVERNANCE_UI_HOST", "127.0.0.1")
    uvicorn.run("idmc_governance.ui.app:app", host=_host, port=_port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
