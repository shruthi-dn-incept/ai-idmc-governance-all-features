# UI Full Coverage Build Spec

**Repo:** `ai-idmc-governance-all-features`

**Status:** final. Self-contained — every open question from earlier revisions is resolved in place and nothing here requires reading a previous draft, the HTML mock, or the handoff doc to act on. Supersedes the six-section router version and the two ladder drafts that followed it.

**Governing principle:** the UI is the product. Every capability claimed in the Aetna comparison document must be demonstrable inside the UI, with no fallback to Claude Desktop or any other client at any point in a live demo.

This is not a completeness preference. It is the core commercial argument. We told Sameer that Claude Desktop is a freeform conversation with no guardrails, and that our value is a structured governance workflow with approval gates. If a follow-up demo has to open Claude Desktop to show scheduling or lineage, that argument collapses in front of his leadership.

---

## Current state

| Metric | Value |
|---|---|
| MCP servers in repo | 6 |
| Servers wired to the UI | 2 (`ai_governance`, `governance_engine`) |
| Servers deployed in docker-compose | 2 |
| Total tools | 65 |
| Reachable from the UI | 29 |
| Not reachable | 36 |

The `STEPS` array in `index.html` currently declares 7 steps. Of the corrected 15, **eleven have a backing route in `app.py` and a result renderer already written in `index.html`**; four do not exist in any form and are new build:

| Ladder step | Backing route | State |
|---|---|---|
| 3 Profile Data | — | **new route + renderer** |
| 7 Recommend Rules | — | **new route + renderer** |
| 9 Schedule Execution | — | **new route + renderer** |
| 11 Monitor Quality | — | **new route + renderer** |

Of those eleven, ten are complete and one is not: `/api/step/create_collection` exists but calls three of step 12's four tools. So the accurate count is **ten complete routes, one partial, four absent**, and every number in this document follows that split.

The eleven routed steps are declaration work, not build work. `SystemDatasetResult`, `CreateCollectionResult`, `PublishMarketplaceResult`, `ConfigureDeliveryResult`, `ConsumerAccessResult`, `VerifyAccessResult` and `WithdrawAccessResult` all exist and are unreferenced.

`app.py` defines two server URLs:

```python
AI_GOVERNANCE_URL     = "http://127.0.0.1:8770/mcp"
GOVERNANCE_ENGINE_URL = "http://127.0.0.1:8765/mcp"
```

`lineage_reporter` (8766), `glossary_manager` (8767), `dq_monitor` (8768) and `data_onboarding` (8769) have no URL constant, no FastAPI route, and no compose service. They are not running anywhere. All four new steps above depend on them.

---

## Architecture

**One ladder. 15 steps. No sections, no nav rail.** Everything that is not one of the 15 is a substep of one of the 15, surfaced through the existing "Run individually" row.

This is settled. The sections below allocate work within it; they do not reopen it.

### The 15 steps

| # | Step | Route | Substeps (tool names) | Gate |
|---|---|---|---|---|
| 1 | Discover Catalog | `discover` | `scan_mcc_source`, `list_connections` | |
| 2 | Scan Table | `scan` | `trace_lineage`, `generate_impact_report`, `find_data_source` | |
| 3 | **Profile Data** | *new* | `create_profile`, `run_profile`, `get_profile_results`, `compute_profile_from_snowflake` | |
| 4 | Generate Taxonomy | `taxonomy` | `generate_governance_taxonomy` | |
| 5 | Domain Structure | `domain_structure` | `create_domain_structure`, `create_system_and_dataset` | **Gate** |
| 6 | Curate Columns | `curate` | `suggest_terms_for_asset`, `create_glossary_term`, `detect_glossary_issues` | **Gate** |
| 7 | **Recommend Rules** | *new* | `create_dq_rules`, `validate_rule`, `list_rule_specifications` | **Gate** |
| 8 | Create DQ Rules | `data_quality` | `create_generic_dq_rules`, `set_dq_occurrences` | |
| 9 | **Schedule Execution** | *new* | `list_mapping_tasks`, `create_mapping_task`, `generate_dq_mapping_task`, `create_schedule`, `create_linear_taskflow`, `run_task`, `get_job_status` | |
| 10 | Publish to Catalog | `mcc_scan` | `run_mcc_scan`, `upload_dq_scores`, `propagate_dq_score` | |
| 11 | **Monitor Quality** | *new* | `get_dq_scores`, `check_score_trends`, `recommend_remediation`, `alert_on_degradation` | |
| 12 | Create Collection | `create_collection` † | `create_cdmp_category`, `create_cdmp_data_asset`, `create_cdmp_data_collection`, `link_asset_to_collection` | |
| 13 | Publish to Marketplace | `publish_marketplace` | `publish_cdmp_collection` | |
| 14 | Configure Delivery | `configure_delivery` | `create_cdmp_usage_contexts`, `create_delivery_template`, `create_terms_of_use`, `create_delivery_target` | |
| 15 | Consumer Access | `consumer_access` | `create_consumer_access`, `approve_consumer_order`, `verify_consumer_access`, `withdraw_consumer_access` | **Gate** on approve and withdraw |

Substeps are named by tool throughout this document, not by route. Where a route name differs from the tool it wraps, the route is given in the Route column and nowhere else.

This table is the complete substep list. Every phase section below elaborates on rows here and adds nothing to them; if a tool appears in a phase but not in this table, the table is wrong. Two tools are deliberately absent because they are orchestrators rather than substeps: `profile_and_govern` and `run_governance_pipeline` back the run-all control, and `publish_marketplace_full` backs it across steps 12 and 13. `get_profile_results_direct` is also absent — it is an internal fallback behind `get_profile_results`, not a user action.

† **Step 12's route is short of its ladder step.** `/api/step/create_collection` calls three tools — `create_cdmp_category`, `create_cdmp_data_asset`, `create_cdmp_data_collection`. `link_asset_to_collection` has no route at all. Step 12 needs either an extended route or a fourth substep route of its own. That was build work rather than declaration work, and the only step outside the four new ones that needed any. Closed by Phase 0 item 6.

**Step 10 carries three score-related tools and they are not interchangeable.** No phase section covers step 10 — it ships today — so the distinction is recorded here. `run_mcc_scan` triggers the catalog scan that executes CDQ rule specs against live data and publishes scores as a side effect of the run. `upload_dq_scores` pushes a batch of scores to CDGC directly, without a scan. `propagate_dq_score` pushes a single score to one named asset — a DQRO or a column — resolving the asset by name when no rule occurrence id is given, and carrying dimension, run date and pass/fail row counts. It is the tool to reach for when a score needs correcting or backfilling against one asset, which is the common case after a partial run. All three are `ai_governance` tools and all three are reachable today; only the first two were surfaced in earlier drafts.

### Profile once, at step 3

Profiling is a ladder step ahead of taxonomy, not a substep of rule creation. This is the single most consequential ordering decision in the spec and it is not stylistic.

**Why it sits before taxonomy.** Taxonomy that classifies on column names alone is the specific thing the Opella customer rejected. `taxonomy` currently sends scanned column metadata to the LLM and asks for a domain tree; from step 4 onward it sends column metadata *plus* the step 3 profile, so classification is grounded in what the data actually contains rather than what the column is called. This changes the prompt payload of an existing step, not just the ladder order — treat step 4 as modified, not carried over.

**Two consumers, one run.** Step 4 (taxonomy) and step 7 (rule recommendation) both read the step 3 output. Profiling is the most expensive operation in the ladder and must not run twice.

- Persist the profile in session state keyed by `{connection, schema, table}`, alongside the existing `scan` cache.
- Steps 4 and 7 read from that state and never call the profiling tools directly.
- Step 3 shows a cache-hit state on re-entry — "profiled 12 minutes ago, re-run to refresh" — rather than silently re-executing. `create_profile` already checks for an existing profile server-side, but the UI should not depend on that to avoid a multi-minute re-run.
- If step 3 has not run, steps 4 and 7 must say so and offer to jump to it, not proceed on column names. Step 7 in particular has no fallback: `recommend_dq_rules` is pure reasoning over a profile payload and produces nothing useful from an empty one.

### Interaction contract

Three patterns from `index.html` are load-bearing. Everything added below conforms to them rather than inventing new ones.

**1. Select-then-run.** A step that acts on a thing renders its selector *above* the Run button, in the `bg-slate-50 rounded-xl p-4 border border-slate-300` input card. The step does not assume a selection exists. See the Scan step (`index.html` ~1935): a Schema dropdown whose selection derives the catalog source, then a Table dropdown that appears only when the schema is not `__ALL_SCHEMAS__`. Both dropdowns fall back to a free-text `<input>` when the upstream step returned no options — which is what keeps a step usable when Discover has not run yet.

Every new step and substep that acts on a named asset needs this same treatment: a dropdown populated from upstream state, a text fallback, and a `⚡ All …` bulk option where a bulk run is meaningful.

**2. Expandable tree for hierarchical results.** `DiscoverResult` (~154) is a four-level collapsible tree — Catalog Source → Database → Schema → tables — held in a single `collapsed` map keyed by path string, toggled by `toggle(key)`. Level 2 renders only when the database name differs from the source name. Table chips come from `SchemaTableList`, which previews 12 and expands via a `+N more` button.

Any result that is a hierarchy uses this, not a flat table.

**3. Run individually.** Composite steps render a `Run individually` label above a wrap of substep buttons (~2046). Each button carries its own `subRunning` spinner and colours itself from the substep result: indigo idle, emerald with `✓` on success, red with `✗` on failure. The parent Run button stays available and runs the whole group. This is the pattern every new substep uses — no new container, no accordion.

The parent "Run Step" control stays a small secondary affordance relative to gates: gates get the full-width `bg-gradient-to-r from-emerald-500 to-teal-600` treatment, run controls stay `px-5 py-2.5`.

---

### Build constraints

Three rules that override anything a builder might reasonably infer from the artefacts in circulation.

**Do not retheme.** The existing Tailwind theme is correct as it stands: brand `#6366f1` with `dark: #4338ca` and `light: #818cf8`, the slate palette, `surface` at `#f1f5f9` / `#e2e8f0` / `#cbd5e1`. No new palette, no new type scale, no component library. Every screen described below is built from classes already in use.

**`DomainApprovalPanel` is the reference implementation for every gate.** It is reused with different grouping and different row content, not rebuilt. Four gates in this spec render through it — steps 5, 6, 7, and the approve action on step 15 — and they differ only in what they group by and what each row shows. A second approval component appearing anywhere in the codebase is a defect.

**The HTML mock is a reference for structure, not for markup.** It shows step ordering, gate behaviour, and the intended layout of each step — which panels sit where, what a result panel contains, how a table is columned. Follow it for all of that.

What it is not is a source of code. It was built from class names rather than from components, so its rendered output only approximates the real theme and is wrong in detail. Do not feed it to a code generator and do not port markup from it. Rebuild its layouts using `src/idmc_governance/ui/static/index.html`, which is the source of truth for anything visual: `DiscoverResult`, `SchemaTableList`, `DomainApprovalPanel`, the Scan step input card, the substep row.

The distinction matters in both directions. An earlier reading of this constraint as "ignore the mock visually" produced a step 2 that rendered lineage as a flat list of hops when the mock — and the Phase 4 section below — call for a graph beside a severity-ranked impact table.

---

## Where the 36 unwired tools land

| Was | Now | Placement |
|---|---|---|
| Profiling (6 tools) | **Step 3** | own ladder step |
| `recommend_dq_rules` | **Step 7** | own ladder step |
| Operate | **Step 9** substeps | `list_mapping_tasks`, `generate_dq_mapping_task`, `create_schedule`, `create_linear_taskflow`, `get_job_status` |
| Monitor | **Step 11** substeps | `get_dq_scores`, `check_score_trends`, `recommend_remediation`, `alert_on_degradation` |
| Explore (lineage) | **Step 2** substeps | `trace_lineage`, `generate_impact_report`, `find_data_source` |
| Glossary | **Step 6** substeps | `suggest_terms_for_asset`, `create_glossary_term`, `detect_glossary_issues` |
| Admin — rule library, validate | **Step 7** substeps | `create_dq_rules`, `validate_rule`, `list_rule_specifications` |
| Admin — connections, catalog sources | **Step 1** substeps | `scan_mcc_source`, `list_connections` |
| Admin — export / import | **Settings panel** | `export_assets`, `import_package` — not a ladder step |

### Rule authoring belongs inside the review gate

The three rule-library tools sit on step 7, not step 8, for the same reason the glossary tools sit on step 6 rather than standing alone.

A steward stalls at a review gate for one of two reasons: nothing on offer fits, or the recommender missed something they already know about. Both have to be solvable without leaving the review. On step 6 that means creating a business term when no existing term matches. On step 7 it means authoring a rule the profiler had no basis to recommend — a regulatory constraint, a known upstream defect, a rule that exists for reasons outside the data.

`list_rule_specifications` belongs there too, because "is there already a rule for this column" is a decision-time question. Asking it at step 8, after the selection has been approved, is asking it too late to change anything.

### Export and import are settings, not a step

They go behind a gear icon in the header, opening a slide-over panel. Not step 10, and not anywhere on the ladder.

The test is scope. Every ladder step acts on a `{connection, schema, table}` — one dataset moving through onboarding. Export and import act on the org: bundles of assets promoted between environments, unrelated to whichever table is in flight. A step that ignores the ladder's subject is not a step.

This does not breach the single-ladder rule, because a settings panel is not navigation. It has no status dot, no position in the sequence, no run-all participation, and nothing downstream depends on it. The same panel houses approval policy configuration when that lands, which is the second thing with org scope and no place in a dataset's lifecycle.

### Route naming drift

Three routes no longer match their step label: `data_quality` backs step 8 "Create DQ Rules", `mcc_scan` backs step 10 "Publish to Catalog", and `consumer_access` backs step 15 whose first substep shares the name. Rename the routes or document the mapping in `app.py`. A developer reading `POST /api/step/data_quality` and looking for step 8 will not find it.

---

## Demo readiness: claim to screen traceability

This is the acceptance criteria for the whole effort. Every row of the comparison document sent to Sameer maps to exactly one step or substep that proves it. A phase is not done when the code merges. It is done when the corresponding rows below can be demonstrated live.

### Cloud Data Quality (7 claims)

| Claim in document | Proving location | Status |
|---|---|---|
| Create DQ rule specifications | 7 → `create_dq_rules` | Phase 6 |
| DQ rule recommendations from profiling | Step 7 | Phase 1 |
| On-demand data profiling | Step 3 | Phase 1 |
| DQ remediation recommendations | 11 → `recommend_remediation` | Phase 3 |
| DQ rule template library | 7 → `list_rule_specifications` | Phase 6 |
| Rule validation before deployment | 7 → `validate_rule` | Phase 6 |
| Bulk rule creation at scale | 8 → `create_generic_dq_rules` | Live |

### Cloud Data Integration (4 claims)

| Claim in document | Proving location | Status |
|---|---|---|
| Create mapping tasks | 9 → `generate_dq_mapping_task` | Phase 2 |
| Create execution schedules | 9 → `create_schedule` | Phase 2 |
| Create linear taskflows | 9 → `create_linear_taskflow` | Phase 2 |
| Asset export and import | Settings panel | Phase 6 |

### Cloud Data Governance and Catalog (11 claims)

| Claim in document | Proving location | Status |
|---|---|---|
| Register rule occurrences | 8 → `set_dq_occurrences` | Live |
| Upload DQ scores | 10 → `upload_dq_scores` | Live |
| DQ score trend monitoring | 11 → `check_score_trends` | Phase 3 |
| Score degradation alerts | 11 → `alert_on_degradation` | Phase 3 |
| Create glossary terms | 6 → `create_glossary_term` | Phase 5 |
| Suggest glossary terms for assets | Step 6 | Live |
| Glossary health scanning | 6 → `detect_glossary_issues` | Phase 5 |
| Create domains and subdomains | Step 5 | Live |
| Trace data lineage programmatically | 2 → `trace_lineage` | Phase 4 |
| Impact analysis with severity | 2 → `generate_impact_report` | Phase 4 |
| Find root data sources | 2 → `find_data_source` | Phase 4 |

### Cloud Data Marketplace (2 claims)

| Claim in document | Proving location | Status |
|---|---|---|
| Dataset onboarding automation | Run-all control | Phase 1 |
| Data provisioning and marketplace | Steps 12–15 | Live |

### Platform and Operations (4 claims)

| Claim in document | Proving location | Status |
|---|---|---|
| End-to-end pipeline orchestration | Run-all control | Phase 2 |
| Autonomous unattended operations | Run-all + 9 → `create_schedule` | Phase 2 |
| External AI interface | Architecture, see note below | Live |
| Bulk operations at scale | Step 2 `⚡ All schemas` | Live |

**Note on "external AI interface":** this claim is about the agent being callable from any MCP client. The UI is itself an MCP client, which is the proof. Demonstrate it by showing the server URL configuration and the fact that the same tools serve the UI. Do not open Claude Desktop to prove it. If asked directly, the answer is that the architecture supports any MCP client and the UI is our reference implementation of one.

**Summary:** 9 of 28 claims are demonstrable today. 19 require Phases 1 through 6.

**On the run-all control.** It proves two claims and stays a small secondary control regardless. It is a text-weight button in the header region, not a primary CTA, and it halts at every review gate rather than running through them. With gates now at steps 5, 6, 7 and 15, an unattended run stops four times — that is the intended behaviour and the thing that distinguishes it from a freeform agent. An orchestrator that blows past an approval gate disproves the exact argument the gates exist to make.

Across the marketplace phase it calls `publish_marketplace_full`, which covers steps 12 and 13 in one request. That tool is not a ladder step and must not be wired to one; steps 12 and 13 call `create_collection` and `publish_marketplace` respectively so their status dots stay independent.

---

## Phase 0: Plumbing — SHIPPED

**Status: complete.** Commits `3c695e7`, `735d939`, `ebc9edd`, `8fc0647` on main. Deployed to the Container App and verified live: 15 rungs, 47 substeps, 27 muted, four 501 stubs, `LS_KEY` orphaning confirmed in a cached browser, step 12's four-key composite proven end-to-end. **Not pushed to origin** at time of writing — origin HEAD is `3c6b67c`, where `STEPS` still declares 7 steps. Anything verified by cloning origin reflects the pre-Phase 0 state.

Recorded below as built, not as work outstanding.

1. Four server URL constants in `app.py`, `127.0.0.1` defaults for local development:
   ```python
   LINEAGE_REPORTER_URL = os.getenv("LINEAGE_REPORTER_URL", "http://127.0.0.1:8766/mcp")
   GLOSSARY_MANAGER_URL = os.getenv("GLOSSARY_MANAGER_URL", "http://127.0.0.1:8767/mcp")
   DQ_MONITOR_URL       = os.getenv("DQ_MONITOR_URL",       "http://127.0.0.1:8768/mcp")
   DATA_ONBOARDING_URL  = os.getenv("DATA_ONBOARDING_URL",  "http://127.0.0.1:8769/mcp")
   ```
   **These defaults are for `app.py` only and must never appear in `docker-compose.yml`.** Inside the UI container, `127.0.0.1` resolves to the UI container. Compose overrides them with service names, following the pattern already in the file:
   ```yaml
   governance-ui:
     environment:
       GOVERNANCE_ENGINE_URL: "http://governance-engine:9765/mcp"
       AI_GOVERNANCE_URL:     "http://ai-governance:9770/mcp"
       LINEAGE_REPORTER_URL:  "http://lineage-reporter:9766/mcp"
       GLOSSARY_MANAGER_URL:  "http://glossary-manager:9767/mcp"
       DQ_MONITOR_URL:        "http://dq-monitor:9768/mcp"
       DATA_ONBOARDING_URL:   "http://data-onboarding:9769/mcp"

   lineage-reporter:
     environment:
       LINEAGE_MCP_PORT: "9766"
   ```
   Note the remap. Source defaults are 8765–8770; compose runs the stack on the 97xx range by setting each server's port variable. A new service needs both halves — a service-name URL on the UI and its own port variable — or it binds one port and is addressed on another. `preflight.sh` fails the build if `127.0.0.1` appears in compose against a server port.
2. Four compose services mirroring the `governance-engine` definition, each with its port variable set.
3. Health indicator polls all six servers and names any that are down. During a demo, a silent failure is worse than a visible one.
4. All 15 steps declared in the `STEPS` array, each carrying the substep list from the ladder table above — that table is authoritative and complete. Ten bound to routes that were complete; step 12 bound to a partial one, closed by item 6. Step 10 carries three substeps, not two: `propagate_dq_score` was an existing `ai_governance` tool with no UI route, so surfacing it was declaration work like the rest of this item.
5. Four missing routes — `profile`, `recommend_rules`, `schedule_execution`, `monitor_quality` — as stubs returning 501, so the ladder renders complete and each phase fills one in. A ladder with four dead rungs is worse in front of a client than four rungs that say "next release".
6. `/api/step/create_collection` extended to call `link_asset_to_collection` last, plus a standalone route backing the fourth substep button. This was the only pre-existing route short of its ladder step, and it landed inside Phase 0 rather than with the marketplace work — see the note below.
7. Profile state in the session store, keyed by `{connection, schema, table}`, readable by steps 4 and 7. Owned by the UI backend at `state/profile_state.json`, gitignored, with `GET/PUT /api/profile_state`. Neither `governance_engine` nor `ai_governance` is a natural owner, and the UI is the only component that already knows about both.
8. Header gear icon wired to an empty slide-over panel. Phase 6 fills it; having the affordance present from the start keeps export and import off the ladder while they are unbuilt.

**There is no port collision.** An earlier revision of this document claimed `ai_governance.py` and `governance_engine.py` both defaulted to 8765 and listed resolving it as Phase 0 work. They do not. Every server reads its own variable with a distinct default: `AI_GOVERNANCE_MCP_PORT` 8770, `GOVERNANCE_MCP_PORT` 8765, `LINEAGE_MCP_PORT` 8766, `GLOSSARY_MCP_PORT` 8767, `DQ_MONITOR_MCP_PORT` 8768, `DATA_ONBOARDING_MCP_PORT` 8769. All six distinct. The claim is recorded here as retracted because it survived several revisions unchecked and would otherwise be reintroduced.

**Done when:** all six servers report healthy in the UI header, and all 15 steps render — eleven functional, four stubbed. *Met.*

**Does step 12 count as functional?** Not on arrival, yes on exit. Its route covered three of four tools when Phase 0 started, and item 6 closed the gap before Phase 0 ended, so it is one of the eleven in the done-when above but not one of the ten in item 4. The distinction matters because the partial state fails silently: step 12 creates a collection, reports success, lights its status dot green, and links no asset. A steward would only discover it at step 13 or later, in front of a client. That is why item 6 sat in Phase 0 rather than waiting for the marketplace phase — a step that looks like it worked is worse than a step that is visibly stubbed.

---

## Phase 1: Profiling and rule recommendation

The capability Anurag asked for by name, the answer to the Opella objection, and the largest change to the demo narrative. Six tools exist in `governance_engine`, none reachable. Delivers two ladder steps and modifies a third.

**Step 3 — Profile Data**

| Concern | Detail |
|---|---|
| Tools | `create_profile`, `run_profile`, `get_profile_results`, `compute_profile_from_snowflake` |
| Selector | table dropdown populated from step 2, text fallback |
| Result | per-column statistics: null counts, distinct counts, min and max, inferred patterns |

Column results are hierarchical — table → column → statistics — so use the `DiscoverResult` tree, not a flat grid. Profiling is asynchronous: `execute` returns a job id and the UI polls `GET {PROFILING_API_BASE}/job/{job_id}`. Drive the existing `stepProgress` bar from the poll.

`compute_profile_from_snowflake` bypasses the Informatica profiling service and queries the warehouse directly. It is the fallback for when a connector cannot enumerate columns. Expose it as a "compute locally" toggle on the step, not a separate substep, and label which path produced the statistics in the result panel — the two have different fidelity and a steward reading the numbers needs to know which they are looking at.

`get_profile_results_direct` stays an internal fallback, not a user action.

**Step 4 — Generate Taxonomy (modified)**

Extend the payload sent to the LLM with the step 3 profile. Surface the change in the result panel: each proposed classification shows the profiling evidence behind it, the same way step 7 does. This is what makes the Opella answer demonstrable rather than assertable — the screen shows that the domain assignment came from data, not from a column name.

Gate the step on step 3 having run.

**Step 7 — Recommend Rules**

`recommend_dq_rules`, reading step 3 state. Renders as a gate using the `DomainApprovalPanel` pattern — see Gate 3 below. Each recommendation shows the column, the proposed dimension, and the profiling evidence ("customer_name is 26 percent null, recommend completeness"). Approving pushes the selection into step 8.

Three substeps stay available while the gate is open, delivered in Phase 6: `list_rule_specifications` to check whether a rule already exists for a column, `create_dq_rules` to author one the profiler had no basis to propose, and `validate_rule` to pre-flight it before it joins the selection. Phase 1 can ship the gate without them; the gate is incomplete until they land.

`profile_and_govern` is the unattended path invoked by the run-all control across steps 3–8, not a separate button.

**Done when:** a profile runs at step 3, taxonomy at step 4 cites profiling evidence in its classifications, and step 7 renders evidence-backed recommendations that flow into step 8.

---

## Phase 2: Step 9 — Schedule Execution

Seven tools, zero current UI surface. Closes the entire CDI column plus two Platform claims.

| Substep | Tools |
|---|---|
| Mapping Task | `list_mapping_tasks`, `create_mapping_task`, `generate_dq_mapping_task` |
| Schedule | `create_schedule` |
| Taskflow | `create_linear_taskflow` |
| Run & Monitor | `run_task`, `get_job_status` |

**Behaviour:**
- Mapping Task uses select-then-run: a rule dropdown populated from step 8, text fallback, then `generate_dq_mapping_task`.
- Schedule offers daily, hourly, weekly and monthly presets, matching the comparison document wording exactly.
- Run & Monitor polls `get_job_status` into a live status list. Reuse `Spinner` and `StatusDot`.
- `run_governance_pipeline` backs the run-all control, not a separate screen.

### Prerequisite: build `M_DQ_Generic`

Every substep in this phase binds to a parameterised mapping template that does not exist until someone builds it. There is no code path: IDMC's mapping creation runs over stateful GWT-RPC, not REST, and cloning is blocked by immutable checksums on inner DTEMPLATE bundles. This is a Mapping Designer task, roughly an hour, and it must be finished before any Phase 2 code is written because everything downstream binds to the resulting mapping ID.

**Parameters — seven, no dollar signs in the names:**

| Name | Type | Purpose |
|---|---|---|
| `Src_Conn` | connection (Snowflake Data Cloud) | source connection |
| `Src_Object` | data object | source table |
| `Tgt_Conn` | connection (Snowflake Data Cloud) | target connection |
| `Tgt_Object` | data object | target table |
| `Rule_Spec` | string | CDQ rule spec FRS ID |
| `Input_Field_Map` | field mapping, string as fallback | e.g. `customer_name=Input` |
| `Source_Filter` | string | optional WHERE clause |

**Canvas — three transformations, Source → Rule Specification → Target:**

- **Source** — Connection → Parameter → `Src_Conn`; Object → Parameter → `Src_Object`; include all fields.
- **Rule Specification** (from the left palette) — Rule → Parameter → `Rule_Spec`; Field Mapping → Parameterized → `Input_Field_Map`; connect Source → Rule Specification. Outputs are all source fields plus `PrimaryRuleSet`.
- **Target** — Connection → Parameter → `Tgt_Conn`; Object → Parameter → `Tgt_Object`; Operation Insert; field map Automatic by name; connect Rule Specification → Target.

Validate, save, take the mapping ID from the URL, set `IDMC_DQ_TEMPLATE_MAPPING_ID` in `.env`. The target table needs the source columns plus `PRIMARYRULESET VARCHAR(100)`.

**Known risk, check it in the first fifteen minutes.** The last recorded attempt stalled because the Snowflake connection did not list tables in Mapping Designer, with JDBC parameters set and the connection test passing. Open the Source transformation and confirm objects enumerate before building anything else. If they do not, that is the escalation, and it is worth more attention than any UI work that day.

**Carry this constraint into the UI regardless:** the Mapping Task substep must detect a missing or unresolvable template on step load and show setup guidance rather than failing at run time. The template will exist for this demo; it will not exist in a client's org on day one.

**Done when:** a schedule can be created and a job triggered and monitored, live, without leaving the ladder.

---

## Phase 3: Step 11 — Monitor Quality

Four tools in `dq_monitor`, server not deployed. Completes the governance loop and supports the audit story.

| Substep | Tool |
|---|---|
| Scorecard | `get_dq_scores` |
| Trends | `check_score_trends` |
| Alerts | `alert_on_degradation` |
| Remediation | `recommend_remediation` |

**The asset selector is a catalog search, not step 10 state.** Monitoring is the one step a steward enters cold — opening the UI on a Tuesday to check whether last week's scores held, with no pipeline run in this session to inherit a selection from. Populate the dropdown from a CDGC asset search, with recently published assets listed first so the common case is one click. Every other step in the ladder can degrade to a text input when its upstream step has not run; step 11 must work with no upstream state at all.

Scorecard and trend render in the result panel; alert configuration is a threshold input in the input card, not a modal — modals are not a pattern in this UI and introducing one for a single screen is not worth the inconsistency.

**Note:** CDGC score rollup is not instantaneous. Uploaded scores may not appear on the asset scorecard immediately. Build an explicit "recently uploaded, awaiting catalog rollup" state so a demo does not look like data loss. This matters more now that step 10 immediately precedes step 11 — the gap between publishing and monitoring is one click, which is exactly the window in which rollup has not happened yet.

**Done when:** an asset scorecard, its trend, and a configured alert threshold all render live.

---

## Phase 4: Lineage substeps on step 2

Three tools in `lineage_reporter`, server not deployed.

| Substep | Tool |
|---|---|
| Lineage | `trace_lineage`, with direction and depth controls |
| Impact | `generate_impact_report`, severity badges low / medium / high |
| Source Finder | `find_data_source` |

**Layout.** Two panels side by side beneath the column metadata table, as in the mock.

Right panel, Impact: a table columned DOWNSTREAM · HOPS · SEVERITY, one row per affected asset, severity as a coloured badge, sorted highest first, with a count of high-severity rows in the panel header. This is the ranked summary and it carries the step.

Left panel, Lineage: **a collapsible tree, not a graph.** Reuse `DiscoverResult` rather than building anything new — same `collapsed` map keyed by path string, same `toggle`, same chevrons, and `SchemaTableList`'s twelve-item preview with `+N more` wherever a node fans out wider than that.

```
sales_monthly_cleaned                    4 up · 6 down
  ▸ Upstream (4)
      ▾ sales_pipeline_job          ← location is the group header
            Command 1
      ▸ sales_weekly
  ▾ Downstream (6)
      ▾ reporting_job
            Command 1
      ▸ monthly_executive_report
```

Group children by `toLocation` / `fromLocation` and put the location on the group header. This is how CDGC's own lineage view is organised, and it disambiguates the `Command 1` collision structurally: Databricks names every notebook command `Command 1`, but each one sits inside a differently-named parent, so grouping resolves what relabelling would only paper over.

**Fetch at `DEFAULT_DEPTH`, render collapsed.** Depth stops being a rendering problem once nothing expands by default, so there is no reason to reduce it — the 21 edges are fetched, and the steward opens the branch they care about. `DEFAULT_DEPTH` is 5 and `MAX_DEPTH` is 20; the depth control still exists for traversing further, not for controlling clutter.

The trade against a graph is real and accepted: a tree duplicates a node that appears in two branches and loses the visual sense of convergence. What it buys is legibility on real pipelines, an existing component instead of a new one, and consistency with step 1, where the steward has already learned this interaction. If a genuine topology view is ever wanted, that is the IDMC lineage visualiser and the spec's position is unchanged: do not rebuild it.

Severity comes from `_classify_severity`: fewer than 5 distinct downstream nodes is LOW, fewer than 20 is MEDIUM, above that is HIGH — and any BI-type asset downstream, meaning a report, dashboard, metric or KPI, forces HIGH regardless of count.

**Asset display names are not unique in CDGC.** Databricks catalogues every notebook command as an asset named `Command 1`, `Command 2` and so on inside its parent job, so a view rendered from `core.name` alone shows a dozen different transformations all reading `Command 1` and appears to route everything through a single node. `_flatten_lineage_hops` already returns `fromLocation` / `toLocation` from `core.location` and `fromId` / `toId` from `core.identity` on every edge; the tree layout below groups on location, which resolves this. `distinct_nodes` counts by identity and is correct regardless — it is only the label that collapses.

Placed on step 2 for two reasons, one mechanical and one about how the step is used.

**Lineage does not come from step 10.** `run_mcc_scan` defaults to `capabilities: ["Data Quality"]` — it executes CDQ rule specs linked to rule occurrences against live data and publishes scores. It does not populate lineage. Lineage comes from the upfront Metadata Extraction and Relationship Discovery scan that is a prerequisite to step 1: `scan_mcc_source` is read-only and consumes a catalog that has already been populated. If a table is discoverable at step 1, the metadata scan has already run, and whatever lineage exists is already there to trace. Nothing between steps 2 and 10 adds to it.

The TPC-DS result during testing is the proof and should be read carefully: those tables were discoverable but returned empty lineage. Metadata extraction had run — that is why they appeared at all — but there were no CDI mappings for Relationship Discovery to derive dataflow from. Discoverability and lineage populate from the same upfront scan and neither is produced by the ladder.

**Step 2 is where the question gets asked.** Where does this table come from, and what breaks if I change it, are provenance questions a steward asks while looking at a table's columns and deciding whether to onboard it — not eight steps later after publishing. Putting the answer next to the column metadata is what makes it useful rather than a capability tab.

A node-and-edge rendering is sufficient. Do not rebuild the IDMC lineage visualiser. The claimed differentiator is programmatic access and severity classification, not graphics. Upstream traces are a hierarchy and should reuse the expandable tree.

**Required empty state:** lineage returns nothing for assets with no cataloged dataflow. This happened during testing on the TPC-DS sample tables and read as a broken feature. The copy must distinguish the two causes, because they need different fixes: either Relationship Discovery has not been run on the catalog source, or it has and there are no CDI mappings touching this asset to derive dataflow from. Neither is fixed by advancing the ladder, so do not offer a "run a scan" button that would not help. Demo against an asset that has real lineage.

**Done when:** lineage and a severity-classified impact report render for an asset with cataloged dataflow.

---

## Phase 5: Glossary substeps on step 6

`detect_glossary_issues` is the only genuinely missing capability. The other two `glossary_manager` tools duplicate logic already inside `ai_governance`.

| Substep | Tool |
|---|---|
| Glossary Health | `detect_glossary_issues` |
| Term Management | `suggest_terms_for_asset`, `create_glossary_term` |

Term Management matters beyond the comparison document: it is how a steward creates a term outside the pipeline, which is the common real-world case when an asset does not fit an existing domain. As a substep of Curate it sits exactly where a steward hits that problem — and now that step 6 is itself a gate, creating a missing term is an action available inside the review rather than a reason to abandon it.

**Done when:** a health scan returns duplicates, orphans and definition gaps, and a term can be created without advancing the ladder.

---

## Phase 6: Rule authoring and the settings panel

**Ladder substeps**

| Substep | Parent | Tool |
|---|---|---|
| `list_connections` | Step 1 | list IDMC connections — call with `top=0` so the client-side trim is skipped and every connection in the org renders, not the default 50 |
| `scan_mcc_source` | Step 1 | catalog source metadata scan |
| `list_rule_specifications` | Step 7 | existing rules in the org |
| `create_dq_rules` | Step 7 | author a custom rule |
| `validate_rule` | Step 7 | pre-flight before save |

`create_dq_rules` here means authoring a rule from a natural-language description, distinct from the templated `create_generic_dq_rules` at step 8. This is what proves the "create DQ rule specifications" claim in its strongest form. `validate_rule` runs as a pre-flight check before the rule joins the step 7 selection, proving "rule validation before deployment." `list_rule_specifications` should also surface the seven templates in `examples/`, proving "DQ rule template library" — they are local JSON rather than API objects, so label the difference.

All three land inside the step 7 gate, not beside it. A steward who has to leave the review to check for an existing rule, then return and re-approve, has been given a worse tool than a spreadsheet.

**Settings panel**

| Screen | Tools |
|---|---|
| Export / Import | `export_assets`, `import_package` |

Behind the header gear icon, in the slide-over panel shelled in Phase 0. Both tools are asynchronous with a poll-then-download pattern, so budget for a progress state and a download button rather than an inline result. The panel is where approval policy configuration goes when it lands.

**Done when:** a custom rule can be authored from plain English, validated, and added to the step 7 selection without leaving the gate, and an export bundle can be downloaded from the settings panel.

---

## Phase 7: Consumer access gates

Step 15 is the only ladder step no other phase touches, which is how it came to hold two ungated actions. It is also the step with dual control and the one irreversible operation in the product, so it is the last place that should inherit a default.

| Surface | Tool | Gate |
|---|---|---|
| Request | `create_consumer_access` | — |
| Approve | `approve_consumer_order` | **Gate 4** |
| Verify | `verify_consumer_access` | — |
| Withdraw | `withdraw_consumer_access` | **Gate 5** |

Both gates render through `DomainApprovalPanel` like the rest, with a single-item selection rather than a list. `auto_approve_access` remains a route and stays unwired from the run-all control.

**Done when:** an access order can be approved only through a confirmation naming the requester, the asset and the delivery target, and a withdrawal only through a red confirmation naming the consumer losing access.

---

## Approval gates

Carried over and made explicit against the ladder. These are the structured-workflow differentiator and the thing Sameer responded to. They stay prominent — full-width, emerald gradient, item count in the label — and the run-all control stops at each one.

**Owning phases.** Gate 1 ships today. Gate 3 is built in Phase 1 alongside the step 7 recommendation panel. Gate 2 is built in Phase 5, which already delivers step 6's three substeps and is where the "no term fits" path gets solved. Gates 4 and 5 are built in Phase 7. Until a gate's phase lands, its step declares the flag but keeps its current run behaviour, and the flag must say which release makes it real — a review badge on a step that does not review is a promise the UI does not keep.

### Gate 1 — Domain Structure (step 5)

The existing `DomainApprovalPanel` is the reference implementation and should not be re-styled:

- Amber `✋ Review before creating in CDGC` banner above everything.
- `{selected.size} / {items.length} selected` with Select all / Deselect all.
- Three grouped sections — Domain, SubDomain, BusinessTerm — each with a group checkbox and per-group count.
- Per-item checkbox, inline rename on click with Enter to commit and Escape to cancel, parent chip on the right.
- Deselected rows go `bg-slate-50 opacity-50` rather than disappearing.
- Full-width `✓ Approve & Create ({selected.size} items)` button, disabled at zero selection.
- The step's normal Run button is suppressed entirely while the gate is open (`index.html` ~2092).

### Gate 2 — Curate Columns (step 6) — built in Phase 5

Same panel, grouped by match confidence, one row per column showing the column name, the proposed business term, and the basis for the match. Approving writes the column-to-term links; deselected columns are left unlinked rather than linked to a fallback term.

The three step 6 substeps stay available while the gate is open, because "none of these terms fit" is the common reason a steward stalls here: `detect_glossary_issues` to see whether the glossary itself is the problem, `suggest_terms_for_asset` to re-ask, and `create_glossary_term` to write the missing term without abandoning the review.

### Gate 3 — Recommend Rules (step 7, new in Phase 1)

Same panel, different groups: group by DQ dimension rather than by asset type. Each row carries the profiling evidence as secondary text. Approving pushes the selection into step 8 rather than writing to CDGC directly.

The three step 7 substeps stay available while the gate is open, for the mirror-image reason to step 6: `list_rule_specifications` answers "is there already a rule for this," and `create_dq_rules` with `validate_rule` covers the rule the profiler had no basis to propose. A rule authored here joins the selection and is approved with the rest, so one approval record covers everything written.

### Gate 4 — Approve (step 15 substep) — built in Phase 7

`approve_order` fulfils a consumer's data access request. It is an authorisation decision and must not be reachable by clicking through. Show requester, requested asset, and delivery target before the approve action. Note that `auto_approve_access` exists as a route and chains order creation with approval — it is a demo convenience and must never be what the run-all control calls, for the same reason the gates exist.

### Gate 5 — Withdraw (step 15 substep) — built in Phase 7

Destructive and irreversible from the UI. Red rather than emerald, with the affected asset and consumer named in the confirmation label.

---

## Consolidation decisions

| Item | Issue | Recommendation |
|---|---|---|
| `publish_marketplace_full` | superset of steps 12 and 13 combined | not a step; backs the run-all control across both |
| `system_dataset` | not a ladder step | substep of step 5, where Domain Structure already registers the source system and dataset |
| `list_catalog_sources` | superseded by `list_catalog_tables` | leave unexposed |
| `data_onboarding.onboard_dataset` | superseded by the ai_governance pipeline | retire the server or keep as an API-only entry point |
| `glossary_manager` term tools | duplicate ai_governance logic | pick one canonical implementation before both drift |
| `register_in_cdgc` vs `set_dq_occurrences` | two paths to the same outcome | document which is canonical |
| `get_profile_results` vs `_direct` | fallback variant | keep both in code, expose one |
| `auto_approve_access` | bypasses the step 15 approve gate | keep as a route, never wire to run-all |

---

## Sequencing

| Order | Phase | Claims unlocked | Rationale |
|---|---|---|---|
| — | Phase 0 | 0 | **SHIPPED** — `3c695e7`, `735d939`, `ebc9edd`, `8fc0647`, deployed and verified live |
| 2 | Requirement 7 | 0 | remediation, not a phase — composite status dots currently report success over total failure |
| 3 | Phase 1 | 3 | named client ask, answers the Opella objection, delivers two ladder steps; carries cross-cutting requirements 1 and 2 |
| 4 | Phase 2 | 5 | largest single gap, the column where CLAIRE GPT scores zero |
| 5 | Phase 3 | 3 | completes the governance loop and the audit story |
| 6 | Phase 4 | 3 | visible capability, low integration risk |
| 7 | Phase 5 | 2 | |
| 8 | Phase 6 | 4 | |
| 9 | Phase 7 | 0 | closes the two ungated actions on step 15; no new claims, but the dual-control story depends on it |

### Demo track — compressed ordering

The table above is the order that minimises risk. A demo commitment overrides it, and the compressed order is different because it is driven by dependency depth rather than claim count.

| Day | Work | Why here |
|---|---|---|
| 1 | **`M_DQ_Generic` built in IDMC Mapping Designer** · four MCP servers answering HTTP in the container · requirement 7 · cross-cutting requirement 1 | The template is a GUI task with no code path and it gates all seven of Phase 2's substeps, so it goes first and in parallel with the server work. Phases 3, 4 and 5 are unreachable until the servers serve. The two cross-cutting items are the failures that discredit a working demo rather than merely limiting it. |
| 2 | Phase 4 (3 substeps) · Phase 5 (3) · Phase 3 (4) | Read-mostly CDGC calls against APIs already proven in this codebase. Ten substeps, lowest integration risk. |
| 3 | Phase 1 (steps 3 and 7) · Phase 6 rule library (3) | Highest narrative value, and FRS rule creation is already reverse-engineered and working. |
| 3 | Phase 2 (7 substeps) | Mandatory. Its runtime dependency, the `M_DQ_Generic` mapping template, is built on day 1 as a prerequisite — see below. |

Under compression, requirement 7 and cross-cutting requirement 1 rise in priority rather than falling. A composite status dot reporting success over four failed tools, or a session expiring mid-demo, are the two failure modes that turn a working product into a credibility problem — and session expiry is already named in this document as the single most likely demo failure.

Phases 0 through 2 take demonstrable coverage from 9 of 28 claims to 17. All six phases take it to 28 of 28, which is the point at which the comparison document and the product are the same thing.

Phase 1 is also the only phase that changes the behaviour of a step that already ships. Step 4 currently classifies on column names; after Phase 1 it classifies on profiled data. If Phase 1 slips, step 4 continues to do the thing Opella rejected, so it should not be resequenced behind Phase 2 on the grounds that Phase 2 unlocks more claims.

---

## Cross-cutting UI requirements

**Owning phases.** These had none, which is how requirement 1 reached a live container unbuilt. Requirement 7 is **remediation, taken standalone before Phase 1** — it fixes a defect that is live now, and it is small enough that batching it behind a phase only extends the window in which the UI reports success it did not have. Requirements 1 and 2 are built in **Phase 1**: it is the first phase with asynchronous work and sustained live API calls, and both are infrastructure the later phases consume rather than reimplement. Requirements 3 to 6 are per-screen obligations, discharged by whichever phase builds the screen; a phase is not done when its screens render, it is done when its empty and unmet states render too.

1. **Session expiry handling on every step.** 401 and 403 with `REPO_38205` means the session died. One shared interceptor in `apiPost`, one "reconnect" affordance.
2. **Async polling pattern.** Profiling, export, import, and job runs are all fire-then-poll. Build one reusable poller rather than four. The existing `stepProgress` bar is the display surface.
3. **Empty states that explain.** Lineage with no cataloged dataflow, scores awaiting rollup, and profiling on a connector that cannot enumerate columns all return legitimately empty results. Each needs copy explaining why, or it reads as a broken feature in front of a client. The Scan step's red "Tables not found in catalog" panel is the model.
4. **Name-to-identity resolution is a hidden first call.** Most CDGC tools resolve a friendly name to an asset id before doing real work. When resolution returns nothing, say "asset not found in catalog," not "no results."
5. **Every selector degrades to text.** Dropdowns populate from upstream step state. When the upstream step has not run, the control becomes a free-text input rather than an empty disabled dropdown — this is what makes any step independently runnable, which is what makes the ladder demoable out of order.
6. **Upstream dependency states are explicit.** Steps 4 and 7 depend on step 3, step 9 on step 8, step 11 on step 10. Where a dependency is unmet, say which step to run and offer to jump to it — the pattern already exists at `index.html` ~2111, where Domain Structure tells the user to run Generate Taxonomy first. The step 2 lineage substeps are the exception: their dependency is the upfront Relationship Discovery scan, which sits outside the ladder entirely, so the unmet state points at catalog configuration rather than at another step.

7. **Composite status is derived from results, not from HTTP completion.** A step that fans out to several tools — 5, 8, 12, 14, 15 — must compute its status dot from the per-tool outcomes it returns, not from the request having finished. Today step 12 turns green when all four of its tools have failed, because the call completed; the rows carry the truth and the dot contradicts them. This is the same defect class as the silent success that Phase 0 item 7 eliminated, one layer up: item 7 stopped a tool from being skipped, and this stops four failed tools from reading as a success. The dot is what a steward scans; the rows are what they read afterwards, if the dot gives them a reason to.

---

## Standing constraint

Until all six phases land, the honest position in any live demo is that the capability exists and is being surfaced in the next release, not that it can be shown in another tool. Do not open Claude Desktop in front of this client. The moment we do, the structured-workflow argument we made in writing stops being credible.

---

# Appendix: API reference for the 36 unwired tools

Extracted from the server source in this repo, not from documentation. Use this to understand what each substep is actually calling, what can fail, and what error states the UI needs to handle. Carried over unchanged.

## Base URLs and auth

| Constant | Value | Auth helper | Header |
|---|---|---|---|
| v2 (CDI) | `https://{POD}.dmp-us.informaticacloud.com` | `_request_v2` | `icSessionId` |
| v3 (platform) | `https://{POD}.dmp-us.informaticacloud.com` | `_request_v3` | `INFA-SESSION-ID` |
| `CDGC_API_BASE` | `https://cdgc-api.dm-us.informaticacloud.com` | `_request_cdgc` | `Authorization: Bearer <JWT>` + `X-INFA-ORG-ID` |
| `PROFILING_API_BASE` | `https://usw1-dqprofile.dmp-us.informaticacloud.com/profiling-service/api/v1` | `_request_cdgc` | Bearer JWT |
| FRS | `https://{DQ_HOST}/frs/api/v1` and `/frs/v1` | direct | `icSessionId` |
| Rule service | `https://{DQ_HOST}/rule-service/api/v1` | direct | `icSessionId` |

Three auth patterns are in play. `_jwt()` mints and caches the CDGC bearer token. Session expiry surfaces as HTTP 401 or 403 with `REPO_38205`. **Every step needs a re-auth path**, because a session that expired overnight is the most likely demo failure. Surface it as "session expired, reconnect" rather than a raw 403.

## Profiling tools

| Tool | Calls |
|---|---|
| `create_profile` | `GET /api/v2/connection/{connection_id}` (resolve connection)<br>`GET {PROFILING_API_BASE}/profile` (check for existing)<br>`POST {PROFILING_API_BASE}/profile` (create definition)<br>`POST {PROFILING_API_BASE}/profile/{profile_id}/execute` (run it) |
| `run_profile` | `POST {PROFILING_API_BASE}/profile/{profile_id}/execute`<br>`GET {CDGC_API_BASE}/data360/search/v1/assets` (resolve table identity)<br>`GET {CDGC_API_BASE}/data360/search/v1/assets/{table_identity}`<br>`GET {CDGC_API_BASE}/data360/search/v1/assets/{column_identity}` |
| `get_profile_results_direct` | `GET {PROFILING_API_BASE}/profile`<br>`GET {PROFILING_API_BASE}/profile/{profile_id}`<br>`GET {PROFILING_API_BASE}/job/{job_id}` |
| `get_profile_results` | delegates to `get_profile_results_direct` |
| `compute_profile_from_snowflake` | `GET /api/v2/connection/{v2_id}`<br>`GET /api/v2/runtimeEnvironment`<br>`GET {PROFILING_API_BASE}/profile`<br>plus direct Snowflake query via `common/snowflake.py` |
| `recommend_dq_rules` | no HTTP; pure reasoning over profile output |
| `profile_and_govern` | orchestrator; chains the above |

**Notes for the UI.** `create_profile` checks for an existing profile before creating, so re-running a substep is safe. Profiling is asynchronous: `execute` returns a job id and the UI must poll `GET {PROFILING_API_BASE}/job/{job_id}` rather than expecting results inline. Show a progress state.

`compute_profile_from_snowflake` bypasses the Informatica profiling service and queries the warehouse directly. It is the fallback for when a connector cannot enumerate columns. Expose it as "compute locally" rather than a separate substep, and make clear in the result panel which path produced the statistics, because the two have different fidelity.

`recommend_dq_rules` makes no API call at all. It reasons over whatever profile payload it is given. This means the Recommend Rules substep depends entirely on Profile Data having succeeded, so gate it rather than letting a user reach it with empty input.

## Execution tools

| Tool | Calls |
|---|---|
| `list_mapping_tasks` | `GET /api/v2/mttask` |
| `create_mapping_task` | `POST /api/v2/mttask/` |
| `generate_dq_mapping_task` | `GET /api/v2/mapping/{template_mapping_id}` (read template params)<br>`POST /api/v2/mttask/` |
| `create_schedule` | `POST /api/v2/schedule/` |
| `create_linear_taskflow` | `POST /api/v2/workflow`<br>`GET /public/core/v3/export/{job_id}`<br>`GET /public/core/v3/import/{job_id}` |
| `run_task` | `POST /api/v2/job` |
| `get_job_status` | `GET /api/v2/activity/activityMonitor?runId={run_id}` (running)<br>falls back to `GET /api/v2/activity/activityLog?runId={run_id}&taskId={task_id}` (completed) |
| `run_governance_pipeline` | chains rule creation, `POST /api/v2/mttask/`, `POST /api/v2/schedule/`, `POST {CDGC_API_BASE}/ccgf-contentv2/api/v1/publish`, `POST /api/v2/job`, score upload |

**Notes for the UI.** `generate_dq_mapping_task` reads the template mapping first to discover its parameter contract, then binds values. If `M_DQ_Generic` is absent from the org, the `GET /api/v2/mapping/{id}` fails and everything downstream fails. Detect this on step load and show setup guidance.

`get_job_status` deliberately hits two endpoints in fallback order because a running job lives in the activity monitor and a finished one moves to the activity log. Poll the monitor first, fall back to the log, and do not treat an empty monitor response as failure.

`create_schedule` requires `startTime` in `.000Z` format. A malformed timestamp is accepted at the API boundary and fails silently later. Validate in the form.

## Monitoring tools

| Tool | Calls |
|---|---|
| `get_dq_scores` | `POST {CDGC_API_BASE}/data360/search/v1/assets` (resolve name to id)<br>`GET {CDGC_API_BASE}/data360/search/v1/assets/{asset_id}?segments=dataQuality:all` |
| `check_score_trends` | same, plus `?segments=summary`; trend computed client side |
| `recommend_remediation` | reads scores via the above; recommendation is reasoning, not an API call |
| `alert_on_degradation` | **no Informatica API.** CDGC does not expose programmatic alert registration. Writes to a local config file (`.dq_monitor_alerts.json`) |

**Notes for the UI.** `alert_on_degradation` is the one place where the product does something outside Informatica. Be precise about this on screen: the threshold is stored by our platform and evaluated by our scheduler, not registered in CDGC. If Sameer's team asks where the alert lives, the answer should already be visible in the interface.

Score rollup lags. A score uploaded seconds ago will not appear under `segments=dataQuality:all` immediately. Build the "awaiting catalog rollup" state.

## Lineage tools

| Tool | Calls |
|---|---|
| `trace_lineage` | `POST {CDGC_API_BASE}/data360/search/v1/assets` (resolve)<br>`GET {CDGC_API_BASE}/data360/search/v1/assets/{asset_id}?segments=lineage-direction:{INBOUND\|OUTBOUND\|ALL}` |
| `generate_impact_report` | same with `lineage-direction:OUTBOUND`; severity computed from edge count and asset types |
| `find_data_source` | same with `lineage-direction:INBOUND`, walked recursively to roots |

**Notes for the UI.** Direction maps as inbound equals upstream, outbound equals downstream. The tool accepts `upstream`/`downstream` aliases and normalises them; keep the user-facing labels as upstream and downstream.

Depth is a client-side traversal parameter, not a server one. Deep traversals mean many sequential calls, so cap the depth control and show progress.

## Glossary tools

| Tool | Calls |
|---|---|
| `create_glossary_term` | `POST {CDGC_API_BASE}/data360/content/v1/assets` |
| `suggest_terms_for_asset` | `POST {CDGC_API_BASE}/data360/search/v1/assets` then column enumeration; suggestion is reasoning |
| `detect_glossary_issues` | `POST {CDGC_API_BASE}/data360/search/v1/assets` filtered to BusinessTerm; duplicate and orphan detection computed locally |

Note the base path difference: term **creation** uses `/data360/content/v1/assets`, term **reading** uses `/data360/search/v1/assets`. Two different services.

## Utility tools

| Tool | Calls |
|---|---|
| `list_connections` | `GET /api/v2/connection` |
| `list_rule_specifications` | `GET {FRS_API}/Documents` filtered by type |
| `create_dq_rules` | `POST {FRS_V1}/Folders('{folder_id}')/Documents` (create shell)<br>`PATCH {FRS_V1}/Documents('{new_id}')` (write rule body)<br>`GET {FRS_API}/Documents('{new_id}')` (verify)<br>`DELETE {FRS_API}/Documents('{new_id}')` (rollback on failure) |
| `validate_rule` | `POST {RULE_SERVICE}/validateRule` |
| `export_assets` | `POST /public/core/v3/export`<br>`GET /public/core/v3/export/{job_id}` (poll)<br>`GET /public/core/v3/export/{job_id}/package` (download ZIP) |
| `import_package` | `POST /public/core/v3/import/package` (upload)<br>`POST /public/core/v3/import/{job_id}` (commit) |
| `scan_mcc_source` | MCC scan trigger via CDGC |

**Notes for the UI.** `create_dq_rules` is a multi-step create with rollback: it creates a document shell, patches the rule body in, verifies, and deletes the shell if the patch fails. Surface it as one substep button with a single spinner. Partial failure leaves no orphan, but the error message should say which stage failed.

Export and import are both asynchronous with a poll-then-download pattern. Budget for a progress state and a download button, not an inline result.

`list_rule_specifications` is what proves the template library claim. The seven templates in `examples/` are local JSON, not API objects, so render them from disk alongside the org's rules and label the difference.
