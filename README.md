# AI-IDMC Data Governance

AI-driven Informatica **IDMC / CDGC** data governance — MCP servers, a web UI, and a
full-catalog **scale pipeline** proven across ~4,000 tables on Azure.

> **New here? This README is your kickstart.** Do the 3 steps in Quickstart, then pick
> a flow (UI or scale pipeline).

---

## Quickstart

```bash
# 1. Configure credentials (IDMC, CDGC, Snowflake, Anthropic)
cp .env.example .env          # then edit .env with your values

# 2. Install the package (editable) — deps come from pyproject.toml
pip install -e .

# 3a. Run the UI locally (starts all six MCP servers + the web UI)
docker compose up             # → http://localhost:9080
#    …or without Docker — run the processes directly (do NOT run start-ui.sh
#    outside a container: it overwrites .env and will refuse to run locally):
python -m idmc_governance.servers.governance_engine &   # :8765 (or .env override)
python -m idmc_governance.servers.lineage_reporter  &   # :8766
python -m idmc_governance.servers.glossary_manager  &   # :8767
python -m idmc_governance.servers.dq_monitor        &   # :8768
python -m idmc_governance.servers.data_onboarding   &   # :8769
python -m idmc_governance.servers.ai_governance     &   # :8770 (or .env override)
python -m idmc_governance.ui.app                        # → http://localhost:9080

# 3b. …or run the full scale pipeline (extract → taxonomy → domain →
#     system/dataset → rule map → DQROs → curate → DQ scan)
python -m idmc_governance.scale.orchestrator --clean
```

Prerequisites: **Python 3.11+**, a populated **`.env`** (see `.env.example`), and — for the
scale pipeline — a Snowflake key (base64 in `.env`; raw keys live in `secrets/`).

---

## Repository layout

```
src/idmc_governance/
├── servers/     6 MCP servers — ALL wired to the UI: governance_engine, ai_governance,
│                lineage_reporter, glossary_manager, dq_monitor, data_onboarding
├── ui/          FastAPI web UI (app.py) + static/
├── scale/       full-catalog pipeline: orchestrator + phase modules
├── common/      shared helpers (paths, snowflake)
└── setup/       one-time data load / catalog-source setup
scripts/         entrypoints (start-ui, start_scale_pipeline, start_bulk_import) + auth helpers
docker/          Dockerfile (scale/servers) + Dockerfile.ui
deploy/          Azure deploy scripts (Container Apps jobs + UI)
docs/            architecture, deployment, demo, scale results
examples/        CDQ rule templates      templates/  CDGC/marketplace import templates
tests/           pytest      secrets/ state/  gitignored (keys / generated artifacts)
```

## Entrypoints

| Flow | Command |
|---|---|
| Web UI | `docker compose up` (local) — the UI has 7 sections: Govern, Marketplace, Operate, Monitor, Explore, Glossary, Admin |
| Scale pipeline (all phases) | `python -m idmc_governance.scale.orchestrator --clean` |
| Bulk-import a DQRO file | `python -m idmc_governance.scale.bulk_import <file.xlsx>` |
| Deploy to Azure | `deploy/azure_deploy_pipeline_job.ps1`, `deploy/azure_deploy_import_job.ps1`, `deploy/azure_deploy_ui.ps1` |

Individual pipeline phases can be run standalone, e.g.
`python -m idmc_governance.scale.extract_columns`, `…scale.rule_map`, `…scale.curate`.

## Docs
- `docs/DEPLOYMENT.md` — architecture + deployment
- `docs/azure-reference-architecture.md` — Azure reference architecture
- `docs/SCALE_RESULTS.md` — measured full-catalog scale results
