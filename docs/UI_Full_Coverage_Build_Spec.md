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

**Step 1 renders collapsed.** `DiscoverResult` initialises `collapsed` as `React.useState({})`, and since `isOpen = !collapsed[key]`, an empty map means every source, database and schema is open on load. That was fine against a small development catalog; on the live container it renders 12 sources and 6,040 table chips at once. Invert the default: sources collapsed, expanding a source reveals its schemas collapsed, expanding a schema reveals its tables. The two summary cards stay.

Two additions to the same step, both taken from the mock, which is right about them. Put a **last scanned** value on each source header beside the existing "N tables · M schemas" — scan recency is governance information and the tree currently drops it.

Distinguish two reasons that value can be missing, because they mean opposite things. If the API returns no field, omit it. If the source exists but has never been scanned, render `never` in amber, as the mock does. A registered-but-unscanned source is the row a steward most needs to see, and silently omitting its date makes it the least visible thing on the screen — the same failure the `ui/discover: surface registered sources with zero keyword hits` commit was written to fix. And give each schema row a **Select** action that sets step 2's schema dropdown and navigates there; the tree browses but cannot act, so today a steward reads it and then re-picks the same schema from a dropdown one step later.

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

**0. The selection is a triple, carried forward — but the catalog does not supply the database.** Every step that acts on a dataset acts on `{database, schema, table}`, and the selection is made once at step 2 and inherited by every step after it.

The database is the problem, and an earlier revision of this document got it wrong. It claimed `catalog_sources_grouped` is `source → database → schema → tables`. It is not. Discover returns `database: ''` for every source, because the catalog stores each asset's external id as `connection/schema/table` with no database segment. What step 1 labels a "source" — `DQ_TEST`, `GOVTEST_BILLING`, `GOVTEST_CLINICAL` — is a **schema**. The real Snowflake database each schema lives in is nowhere in the catalog payload.

This breaks any design that reads the database from discover. It cannot be read from discover; it is not there. Two consequences follow, and both are load-bearing:

- Step 2's top control is **Source → Schema → Table**, and "Source" is honest — it is what the catalog provides. It is not a database picker.
- The database must come from a **schema-to-database map the application holds**, because the catalog cannot provide it. `SHOW DATABASES` plus the schema layout gives the authoritative mapping:

  | Database | Schemas |
  |---|---|
  | `INCEPT_GOV_DEV` | `DQ_TEST`, `DQ_FRAMEWORK` |
  | `GOVERNANCE_SCALE_TEST` | `GOVTEST_CLAIMS`, `GOVTEST_CLINICAL`, `GOVTEST_MEMBER`, `GOVTEST_PROVIDER` |
  | `GOVERNANCE_SCALE_TEST_C` | `GOVTEST_BILLING`, `GOVTEST_ENROLLMENT`, `GOVTEST_RISK`, `GOVTEST_UTILIZATION` |
  | `GOVERNANCE` | `REGISTRY` |
  | `RND` | `RND_CLINICAL` |

  When a schema is selected, its database is **derived from this map and displayed, not typed**. A steward who picks `GOVTEST_BILLING` must not also have to know it lives in `GOVERNANCE_SCALE_TEST_C` — telling them which database a schema belongs to is the UI's job, and is exactly the provenance the ladder exists to surface. Databricks and other non-Snowflake schemas have no entry and no warehouse-path database; the local profile path is disabled for them regardless, so their absence from the map is correct.

  This map is configuration, not catalog. It changes only when a database or schema is added, so it belongs in one named place — a constant the app reads — not scattered across the UI. When the account gains a database, the map gains a row; nothing else changes.

Each control still falls back to free text when its upstream has not run, except the Source/Schema/Table triple on step 2, which stays dropdowns for the reason in rule 5. The derived Database field on step 3 is read-only when the schema is in the map and editable only as an escape hatch for a schema the map does not yet cover.

**Source, Schema and Table are dropdowns; Database is derived, not entered.** See rule 0 — the catalog has no database segment, so there is no database to pick from discover. The three dropdowns are Source → Schema → Table, populating from `catalog_sources_grouped` (`connection → schema → tables`), each filtering the next. The Database shown on step 3 is looked up from the schema-to-database map when the schema is chosen, and displayed read-only.

Dropdowns rather than free text for the same reason throughout: identifier case matters against `INFORMATION_SCHEMA`, and a typed lowercase name returns "not found" indistinguishable from a missing grant. The one editable case is the derived Database, and only when the selected schema is absent from the map — an escape hatch, not the normal path.

This makes step 2 dependent on step 1 having run, which is a departure from rule 5 below. Handle it the way Domain Structure already handles its unmet dependency at `index.html` ~2111: say Discover has not run and offer to jump to it, rather than presenting three empty dropdowns. The consequence to accept knowingly is that a table absent from the catalog cannot be selected, so profiling something before it is onboarded is not possible from this screen.

Note what this does not fix: reaching a database requires the connecting role to hold USAGE on it. A role scoped to one schema makes every other schema return "not found in INFORMATION_SCHEMA", because Snowflake's INFORMATION_SCHEMA only shows what the current role can see — a missing grant and a missing table are indistinguishable. Grant across the databases in scope rather than per schema, or this surfaces as a UI bug every time someone picks a new schema.

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

**The HTML mock is a reference for structure, not for markup.** It shows step ordering, gate behaviour, and the intended layout of every step — which panels sit where, what a result panel contains, how a table is columned. Follow it for all of that; the Step layouts section below records each one so the mock does not have to be opened to build from it.

What it is not is a source of code. It was built from class names rather than from components, so its rendered output only approximates the real theme and is wrong in detail. Do not feed it to a code generator and do not port markup from it. Rebuild its layouts using `src/idmc_governance/ui/static/index.html`, which is the source of truth for anything visual: `DiscoverResult`, `SchemaTableList`, `DomainApprovalPanel`, the Scan step input card, the substep row.

The distinction matters in both directions. An earlier reading of this constraint as "ignore the mock visually" produced a step 2 that rendered lineage as a flat list of hops when the mock — and the Phase 4 section below — call for a graph beside a severity-ranked impact table.

---

## Step layouts

The mock is the layout reference for all fifteen steps. Each entry below is what is on screen, top to bottom. Rebuild every one from `index.html`'s components and theme — the mock's markup is not a source, only its structure.

**One exception, step 1.** The mock renders a flat table there, which the original brief identified as an error and which `DiscoverResult` already does better. Step 1 follows the note under the ladder table instead: collapsed tree, carrying the mock's four data points and its Select action.

### Two gate patterns, not one

Steps 5, 6 and 7 use `DomainApprovalPanel` — a multi-select review of proposed items before anything is written. That remains the single approval implementation.

Steps 10, 13 and 15 use something different and it is not a violation of that rule: a **confirmation modal** for one irreversible action, listing its consequences in plain language before it proceeds. **Built and live** (`d67f933`): modal open/cancel verified, and for step 15 the dual-control queue, header chip, disabled self-approve and red withdraw. What remains for these steps is conformance to the layouts below, not building the pattern. Publishing to the catalog lists what the scan will do and that it cannot be retracted. Publishing to the marketplace lists who will see the collection and that unpublishing does not revoke granted access. Approving access lists what the consumer gains and which identifying columns it includes. These are not selections to review; they are decisions to confirm, and the consequence list is the substance of the screen.

Access granting additionally carries **dual control**: an approval waits for a second approver, a requester cannot approve their own request, and step 15 shows an *Awaiting second approver* panel with a live count.

### The shell

The frame around the steps is as much of the mock as the step bodies are, and the current build differs from it in ways that cost information rather than only appearance.

**Sidebar, 264px, white, right border.**

Header block: a 7×7 rounded indigo square carrying `I`, then `Incept IDMC Agent` in bold with `GOVERNANCE AUTOMATION` beneath it in tiny tracked uppercase slate.

The ladder itself is grouped, not a flat list of fifteen. Two groups — **Governance** for steps 1 to 11 and **Marketplace** for 12 to 15 — each introduced by a small bold uppercase slate label and rendered as a white rounded card with dividers between rows. The grouping is presentational and does not reintroduce sections: there is still one ladder, one sequence, one numbering.

Each row carries four things: a status dot, `N · Label`, any badges, and — the part currently missing — **a second line of per-step status text**. `3 sources · 17 tables` under Discover, `CUSTOMER_POSITIONS · 19 cols` under Scan, `Awaiting steward review` under Domain Structure, `Not started` under anything unrun. That line is how a steward reads pipeline state without clicking through fifteen steps, and a ladder without it is a table of contents rather than a status board. Badges: `NEW` in indigo for steps 3 and 7, `REVIEW` in amber on gated steps, muted slate with a phase tooltip where the gate is declared but unbuilt. A skipped step renders struck through. The active row takes an indigo-50 background.

Sidebar footer: **Reset** and **Run all unattended** as small bordered buttons, with the note *Unattended skips every review gate. Use for reruns, not first passes.* beneath them. This placement is the point — the run-all belongs here, small and annotated, not in the header where it reads as a primary action. The note is doing real work: it tells a steward the control exists for reruns and warns what it bypasses, which is the argument the gates exist to make.

**Header, 56px, white, bottom border.**

Left: an `Incept Data Solutions` pill in slate with a border, then the breadcrumb `N · Label` in semibold slate.

Right, in order: **six individual dots**, one per MCP server, then `6 of 6 online`. Six dots and a count say which server is down at a glance; a single `6/6` string does not, and naming the failing server is what the health indicator was for. Then the `⚑ Awaiting approver N` chip in amber, hidden until a dual-control approval is pending and clicking through to the settings panel. Then `Session · Nm` with its own dot — session age is the leading indicator for the expiry failure this document calls the most likely demo failure, so it belongs on screen. Then the ⚙ opening the slide-over.

Main region: scrolling, 24px padding, content capped at 1080px.

### Per step

**1 · Discover Catalog** — collapsed tree per the ladder-table note. Summary cards above. Last-scanned on each source header; Select on each schema row.

**2 · Scan Table** — card *Column metadata*, columned COLUMN · TYPE · NULLABLE · DESCRIPTION, header pill showing the column count. Only two of those four are available today: the scan tool returns `data_type` but the UI wrapper drops it before forwarding `columns_preview`, so forward it; NULLABLE and DESCRIPTION are not in the scan output at all and render muted — `—` and `none` respectively, as the mock already shows for description. Do not synthesise either. Beneath it, two panels side by side: *Lineage* as the collapsible tree with an `N up · M down` pill, and *Impact if this changes* columned DOWNSTREAM · HOPS · SEVERITY with a high-severity count pill.

**3 · Profile Data** — one card *Profile results*, columned COLUMN · NULL % · DISTINCT · MIN / MAX · READS AS, null bar red above 10%, duplicate badge on DISTINCT. Header carries a `NEW STEP` badge; time the request in the UI if a duration is wanted, since no tool returns one.

On amber highlighting: an earlier revision asked for out-of-range values generally, which is not derivable — no per-column valid range exists anywhere in the profile payload. One narrow case is derivable and is the one the mock shows: a maximum date in the future. Flag that and nothing else. Footer callout: *Why this runs before taxonomy*.

**4 · Generate Taxonomy** — card *Proposed taxonomy*, an indented hierarchy: domain in bold with a `DOMAIN` badge, subdomain indented beneath, terms as muted text below that. **Each classification derived from profiling carries its evidence inline in indigo** — `← classified from 5 distinct codes`. That annotation is the Opella argument rendered on screen and is the most important element on the step; a taxonomy without it is the descriptions he rejected. Header badge `PROFILE INFORMED`. Footer: *Nothing written to the catalog yet. The next step is where you choose*, with a *Review and select →* button.

**5 · Domain Structure** — gate banner *Review before creating in CDGC* / *Deselect anything you do not want. Click a name to rename it.* Then `DomainApprovalPanel`.

**6 · Curate Columns** — gate banner *Review before linking in CDGC* / *Every match was proposed by the agent. Deselect anything wrong, click a term to rename it.* Approve label: *Approve & Link*, counting columns linked to business terms.

**7 · Recommend Rules** — gate banner *Review recommended rules* / *Each recommendation cites the profiling evidence behind it.* Approve label: *Create rules*, counting rule specifications.

**8 · Create DQ Rules** — card *Rules and occurrences*, columned RULE · DIMENSION · COLUMN · OCCURRENCE, occurrence id as an emerald badge, header pill with the created count.

**9 · Schedule Execution** — **the frequency control must not offer intervals it cannot honour.** Weekly and Hourly require a day-of-week selection the UI does not provide, so they were made to create cleanly by defaulting to every day — which means a schedule labelled *Weekly* runs *daily*. That is the same defect as `target_type` on step 14: a control asserting something the backend does not do. Either disable Weekly and Hourly until a day-of-week picker exists, or build the picker; do not ship a label that misstates the schedule. Daily and Monthly are correct as-is.

Two other schedule findings, both fixed and both worth keeping recorded because the error messages pointed away from the causes. The `.000Z` datetime format is **correct** — the v2 API rejects times *without* milliseconds, so the obvious-looking fix of dropping the suffix would break creation. And a repeated schedule name returns an opaque `REPO_10301` "internal error while saving," which is a duplicate-name collision, not an internal fault; names auto-suffix to the next free slot.

Layout: an amber banner at the top reporting template state: *Execution template present. `M_DQ_Generic` found in this org. Tasks below bind to it.* When it is absent the same banner carries the setup guidance instead. Then card *Mapping tasks*, columned TASK · BOUND RULE · SCHEDULE · LAST RUN · [Run], status as a coloured badge, unscheduled shown as muted `none`. Beneath, two panels: *New schedule* with frequency as a segmented control (Hourly · Daily · Weekly · Monthly), a start-time field hinting `.000Z required`, and a create button; and *Job runs* listing run id, task and status, with a `polls every 5s` pill and any failure reason in a red footer.

**10 · Publish to Catalog** — card with one sentence of explanation and a single button opening the confirmation modal. Beneath, three stat cards: terms created, columns linked, rules active.

**11 · Monitor Quality** — four stat cards across the top — composite and each dimension — coloured by band with the delta beneath each. Then two panels: *Score trend* as a line chart with a dashed threshold line, breach points marked, and an amber footer naming the date it crossed and that it has not recovered; and a stacked right column of *Remediation*, each finding carrying a one-line diagnosis and a button that jumps to the step that fixes it, and *Alert threshold* showing the asset, its threshold and a firing badge. The alert panel must carry the note that thresholds are stored and evaluated by this platform because Informatica exposes no programmatic alert registration — that is the one place the product does something outside IDMC and it should be visible, not discovered.

**12 · Create Collection** — card *Collection*: category, collection name, and a data-asset selector showing each asset with its quality score. Create button. **Verified live (`fb84508`):** the route forwards card inputs to `create_cdmp_category`, `create_cdmp_data_collection`, `create_cdmp_data_asset`, `link_asset_to_collection`. Note that `create_cdmp_data_asset` is a **lookup, not a create** — it searches CDMP for an asset CDGC has auto-synced and returns found/not_found. The selector must therefore offer only assets that already exist in CDMP; a table profiled but not yet synced resolves as not_found and the link will not bind. Demo consequence: the asset must be through discovery and synced before step 12, or the link silently fails.

**13 · Publish to Marketplace** — card with one sentence of explanation and a button opening the confirmation modal.

**14 · Configure Delivery** — card *Delivery and terms*: usage contexts as a checkbox list, delivery target as a segmented control (Snowflake share · File · Ticket), save button. Amber footer noting that where a signed acknowledgement is required this step can be removed from the automated flow. **Verified live (`fb84508`):** the route forwards to `create_cdmp_usage_contexts` and `create_delivery_target`. Important limitation, honest in the room: `target_type` is **informational only** — the tool records it but does not send it in the delivery-target POST body, so Snowflake-share, File and Ticket all produce the same marketplace object and **no actual Snowflake share is ever provisioned**. The segmented control labels intent; it does not drive the destination. **CDMP has no hard-delete for usage contexts or delivery templates** — the API exposes only create, update and list for both (DELETE returns 405). Anything created through steps 12 and 14 in a client org is therefore permanent via the tool; removal requires the CDMP console. This is a governance edge worth surfacing to a client before they rely on the flow, not only a test-cleanup detail. Until that is wired (a backend change, not built), the delivery step demonstrates the governance workflow — request, terms, target selection, approval gate — but not the physical provisioning of bytes to a destination. Do not claim live Snowflake delivery from this control; the honest claim is that the marketplace object and access request are created, and provisioning is the next step. The control should either be labelled as recording intent or have non-provisioning options disabled, so the UI does not overclaim.

**15 · Consumer Access** — card *Consumer access lifecycle*, columned CONSUMER · REQUESTED · STATE · ACTION, state as a coloured badge, pending rows offering Approve and fulfilled rows offering Withdraw, each opening its confirmation modal. Header pill `dual control`. Beneath, *Awaiting second approver* with a live count and the note that a requester cannot approve their own request.

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

## The source list must not be derived from asset search

`catalog_sources_grouped` — what step 1 renders — is built by querying `data360/search` for assets and grouping them by source. A source with zero indexed tables therefore produces no assets, no group, and no row: it is silently absent rather than shown as empty. `catalog_source_names` holds the authoritative registry and retains it.

This is one defect behind two symptoms that were investigated separately. Sources missing from Discover, and never-scanned sources not rendering their amber state, are the same mechanism: a source that has never been scanned has nothing in the search index, so the amber row it should produce never exists to be rendered. The rendering code was correct throughout; the payload never contained the row.

It also caused a wrong diagnosis twice in one session — a source was declared not to exist on the basis of Discover output, before the authoritative list was consulted. Any conclusion about what is in the catalog must come from `catalog_source_names`, not from what Discover renders.

**Fix by merging, not replacing.** Keep the asset-search grouping as the primary path — it works and it carries the table lists — and add any source present in `catalog_source_names` but absent from the grouping, rendered with a zero table count and a note that it has no indexed assets yet. This is additive, so existing behaviour is unchanged, and it makes the invisible visible rather than rebuilding the entry point to the whole ladder.

**Search-index lag is a separate constraint and code cannot fix it.** A source can be fully present in CDGC Browse with its tables enumerated, and still be absent from `data360/search` for some time afterwards. During that window the merge above makes the source appear with zero tables — visible, but not drillable, because the table list comes from the same search segment. Whether Browse exposes an enumeration path the application could use instead is worth establishing; until it does, a newly scanned source is not demonstrable however correct the code is.

---

## Client-side state: what may and may not be persisted

**The Discover result must not be written to localStorage.** It serialises to roughly 3.3MB — six thousand tables with their metadata — against a browser quota of about 5MB. Live measurement caught the app attempting a 3,334,443-byte write, receiving `QuotaExceededError`, and swallowing it. The consequence is not a failed save; it is a silent one. In-memory state stays correct and the UI looks healthy, so nothing surfaces until a remount, at which point the app rehydrates from the last write that succeeded — a 236-byte record with an empty `results` object — and every completed step returns to Not started.

Persist only what the UI cannot recompute: the selected `{database, schema, table}`, step statuses, and the small result summaries the sidebar status lines need. Catalog payloads stay in memory and are re-fetched. A legacy `idmc_gov_ui_state` key also occupies ~3.2MB on existing browsers and is not cleared by Reset; clear it explicitly on load.

**A failed persist must be visible.** Swallowing `QuotaExceededError` is what turned a storage limit into silent total data loss. Surface it, and never tell the user state was saved without confirming the write returned.

That last point generalises. The ErrorBoundary fallback reads *"Your session is saved. Reload to recover it"* — a statement that was false at the moment it was written. This is the third instance of the same defect class in this codebase: `target_type` on step 14 labels a delivery destination it does not provision, and step 9's frequency control offered a Weekly schedule that ran daily. **A control or message that asserts something the system does not do is a defect of the same severity as a broken function**, because it is the one class of bug that a user cannot detect. Every assertion in the UI should be traceable to something the code actually guarantees.

**Every step that consumes a stored profile must verify it matches the current selection.** Step 3 does this and is the reference implementation. Step 4 does not, and renders another table's taxonomy: after scanning a second table in step 2, step 4 produced a pharmaceutical-sales domain hierarchy for `CUSTOMER_POSITIONS`, with a green Complete status, no warning, and the `PROFILE INFORMED` badge silently absent. Re-scanning did not clear it; only a full Reset did. Step 7 reads the same store and needs the same guard.

The badge disappearing is the tell worth generalising: when evidence-derived output loses its evidence, the absence of the badge is the signal, and it must fail loudly rather than degrade quietly into an unattributed result.

---

## The step 7 gate does not control step 8

**Established by source reading, 27 Jul.** This is the most consequential open defect in the product, because the claim it breaks is the one the product is sold on.

`create_generic_dq_rules` derives dimensions from each scanned column's data type — varchar gets COMPLETENESS and VALIDITY, a numeric key gets COMPLETENESS and UNIQUENESS, a date gets COMPLETENESS and TIMELINESS. It has no parameter for recommendations. The frontend posts `{}` to `/api/step/dq_rules`; `app.py` builds the call from scan-derived plan parameters only; approvals are never read. `approvedRecs` reaches exactly two places: a display banner and localStorage.

So approving five recommendations, approving none, or approving a different set produces identical output. On `COUNTRY_REF` — four varchar columns, zero recommendations approved — step 8 still created eight occurrences across two rule specs.

**The banner makes it worse.** It renders *"N evidence-backed recommendations approved at step 7 and carried into this run"* — asserting a causal link that does not exist. This is the fifth instance of the UI asserting what the backend does not do, after `target_type`, the Weekly schedule, the ErrorBoundary's save claim, and `get_profile_results`' docstring. It is the most damaging of the five: the others overstate a feature, this one overstates the **differentiator**.

Note also that when nothing is approved the banner does not render at all, so rules appear with nothing on screen indicating they came from a data-type template rather than from evidence.

**What is actually true**, and what any external claim must be limited to: step 7 recommends rules from measured profile evidence; a steward reviews before anything is written; step 8 creates rule specifications in CDGC. What is **not** true is that the rules step 8 creates are the ones approved at step 7.

**The fix** is to pass the approved recommendations from the frontend into the step 8 route, thread them through `app.py`, and give `create_generic_dq_rules` a parameter that, when supplied, drives rule creation from the approved set rather than from the column-type template. The template becomes the fallback for the unapproved path rather than the only behaviour.

**Design ruling: a column with no approved recommendation gets no rule.** Not a template default. This is what makes the gate load-bearing — approve less, create less, and the count on screen changes to match.

The obvious objection is that a clean table then receives no rules at all. That is correct behaviour, and the answer preserves the architecture rather than working around it: if baseline rules are wanted on clean data, **step 7's recommender should propose them**, so they pass through the gate like everything else. Step 8 must never add rules behind the gate that no one approved. The moment it does, the gate is advisory again.

**Consequence to expect, not a regression.** After this lands, `COUNTRY_REF` produces zero recommendations at step 7 and therefore zero rules at step 8. That is the rule working. It also means rule counts, occurrence counts, step 9's bindings, step 10's active-rules card, and step 11's composite and dimension scores all move. Re-baseline once after the change rather than chasing the numbers through the build.

**The banner must become truthful in both directions.** Today it renders only on the approved path, so template-driven rules appear with nothing on screen saying where they came from. After the fix it should state which path produced the rules either way.

This is not a demo-day fix. It is the first thing to build after.

---

## Deployment verification

Two gaps in how builds reach the container, both found the expensive way.

**1. The deploy script continues after a failed build.** On two occasions `az acr build` printed an error and the script proceeded to redeploy the previous `:latest` image. Both times the stale deploy was caught only by grepping the served HTML for expected markers — a check that happens to have been run, not one the process guarantees. Add `if (-not $?) { throw }` after the build step so a failed build cannot silently redeploy. This is the higher priority of the two: a silently stale deploy means shipping code you believe you fixed.

**2. The application cannot report which build it is running.** There is no `/version`, no build string in the served HTML, and `/api/health` returns only MCP server states. Verifying that the container matches `main` therefore requires either deployment logs or fingerprinting the served bundle against expected code markers — the latter took a test agent twenty minutes and returned only circumstantial evidence, because `last-modified` proves recency and not identity.

Expose the commit at `/api/version`. Bake it at image build time — a Docker build argument populated from `git rev-parse --short HEAD`, surfaced as an environment variable the app reads — so the value cannot drift from the artifact. Then deploy verification becomes one request compared against the commit intended, rather than a marker hunt.

**Sequence these deliberately.** The version endpoint requires touching the Dockerfile and the deploy script, which is the same script with the silent-failure history — so the change intended to detect stale deploys could itself cause one if it breaks the build and the script swallows it. Harden the script first, confirm a clean deploy on the hardened script, then add the endpoint. Verify the first deploy after each change more carefully than usual.

Neither is on the demo path and neither is reachable from the UI, so regression risk is near zero. The value is highest before a period of frequent deploys, not after it.

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
| Selector | table dropdown carrying **`{database, schema, table}`**, text fallback |
| Result | one card, per-column statistics |

**The selector must pass database and schema, not just a table name.** `compute_profile_from_snowflake(object_name, database=None, schema=None, …)` resolves `db = database or SNOWFLAKE_DEFAULT_DATABASE` and `sc = schema or SNOWFLAKE_DEFAULT_SCHEMA`, which read `SNOWFLAKE_DATABASE` and `SNOWFLAKE_SCHEMA` and default to `INCEPT_GOV_DEV` and `DQ_TEST` in source. A table selected from anywhere else therefore resolves to the wrong path and fails with "not found in INFORMATION_SCHEMA". The profile cache is already keyed on `{connection, schema, table}`; the call must carry the same triple.

**The env defaults should not be load-bearing, and arguably should not resolve at all.** `INCEPT_GOV_DEV` / `DQ_TEST` are one org's development values compiled into product source, and a call that omits the triple silently succeeds against the wrong database rather than failing. That is the worst available behaviour: it produced a "table not found" error that read as a code bug for an hour when the code was fine and the target was wrong. Once the UI passes the triple on every call, the defaults are dead weight — and they would be better as no default at all, failing with "no database specified" rather than reaching into somebody's dev org. Same applies to `SNOWFLAKE_ROLE` defaulting to `ACCOUNTADMIN`, which is a poor default on its own merits.

None of this needs config per source system. Once the selector carries `{database, schema, table}`, a different table, schema or database requires no change; only a different Snowflake account needs credentials. What does not scale is the connector limit below, which is architectural rather than configurable.

Note the second, subtler config trap: `common/snowflake.py` opens the connection against `SNOWFLAKE_GOVTEST_DB`, which defaults to `GOVERNANCE_SCALE_TEST` — a different variable and a different default from the one the query qualifies with. The connection can succeed against one database while the query fully-qualifies into another, which works only if the role holds USAGE on both. The tool's own error text names this and case sensitivity as the two causes; keep that text visible rather than replacing it with something friendlier.

**`compute_profile_from_snowflake` is Snowflake-only.** It issues SQL against the warehouse directly. For a Databricks, Oracle or any other source it cannot work at all, and the UI must disable the local path with a reason rather than letting it fail. Where it does apply it is the better path: one query, immediate, no CDGC propagation wait, and it returns the exact shape `recommend_dq_rules` expects.

**Wide DECIMAL columns exceed the profiling engine's ceiling.** IDMC's profiling service rejects DECIMAL/NUMBER precision above roughly 28 with `PROFILE_MDL_00006`; Snowflake and Databricks both allow 38, and `CUSTOMER_POSITIONS.EXPOSURE_AMOUNT` is `NUMBER(38,2)` — the demo table triggers this on the Informatica path. The column forward clamps precision to 28 (keeping scale) rather than skipping the column: a column silently absent from a profile reads as a broken tool, and precision does not affect the min/max/null statistics profiling returns. This is a general rule, not a Databricks-specific one — the warehouse path avoids it only because it bypasses IDMC.

**The Informatica path needs a profile to exist first.** `run_profile` fails with "No profile defined for connection=… + object=…" when the connector's metadata-fetch endpoint cannot enumerate columns for an unattended auto-define, which is the case for the v2 Snowflake connector. Someone must create the profile once in IDMC → Data Profiling → New Profile. Treat this as a per-table environmental prerequisite, not a bug, and say so on screen — the error text is accurate and should be surfaced rather than swallowed.

**A failed run must clear the previous result.** Observed live: with `ACCRUAL_BRIDGE_MONTHLY` selected, three substeps failed and `get_profile_results` returned a cached profile for `REF_COMPANY_CODE`, which rendered under the new table's name with a row count from one run and column statistics from another. The give-away was 242 distinct values against 240 rows — impossible within one dataset, and only visible to someone checking.

Two rules follow. A result panel belonging to a different `{connection, schema, table}` than the current selection is never displayed; changing any part of the triple clears it. And `get_profile_results` must be filtered by that triple rather than returning whichever profile the service last created — the cache is already keyed on it, so the lookup should be too.

This matters well beyond the display. Steps 4 and 7 read the stored profile. A profile returned for the wrong table means taxonomy classifies one dataset's columns using another's statistics, and rule recommendations cite evidence that was never measured on the asset they apply to. Both would look entirely plausible on screen.

**Forward the column list from step 2.** `create_profile` fails with "No columns provided for 'X' and CDGC search returned none. Pass columns=[…] explicitly." Its CDGC lookup resolves nothing even for a table step 2 has just scanned and rendered every column of. The UI holds that list already, so pass it — `[{'name', 'dataType', 'precision', 'scale'}]` — rather than letting the tool re-derive what the previous step established. This is the same class of gap as the selector not carrying database and schema: state exists one step upstream and is not handed forward.

**Do not auto-create a profile to work around it.** The backend currently attempts `create_profile` when none is found, which replaces a precise prerequisite message with whatever `create_profile` fails on, and performs a write nobody asked for. Remove the fallback and surface the original error. This is the same class of defect as a status dot reporting success over failure: a silent action producing a misleading result.

Whether this limitation extends beyond the v2 Snowflake connector is untested. The error names that connector specifically, and other connectors may auto-define without any console work — which would invert the picture, leaving Snowflake as the one source needing manual setup while also being the only one with the fast direct path. Worth fifteen minutes against an Oracle or Databricks table before it is repeated to a client.

Profiling through the service is asynchronous: `execute` returns a job id and the UI polls `GET {PROFILING_API_BASE}/job/{job_id}`. Drive the existing `stepProgress` bar from the poll.

`get_profile_results_direct` stays an internal fallback, not a user action.

**Result layout — one card, not one per substep.** Four substeps writing four result cards produces four near-identical panels of zeroes when anything fails. Render a single **Profile results** card, columned:

`COLUMN` · `NULL %` · `DISTINCT` · `MIN / MAX` · `READS AS`

`NULL %` as a small bar with the percentage, turning red above the 10% band `recommend_dq_rules` uses. `DISTINCT` carrying a `N dup` badge — fired on **high-cardinality columns only**, where duplication is genuinely a defect. An earlier revision of this rule said to suppress on code lists and on low-cardinality columns, which was too blunt: it silenced the badge on `POSITION_ID` (18 distinct of 19 rows), the one column the demo depends on, while still firing on others. The correct test is a **distinct-to-row ratio above roughly 0.9** — a column that is nearly unique but not quite is an identifier with duplicates, which is a real finding. A column at 4 distinct of 1,000 is a code list, not a defect, and gets no badge. Do not key the suppression off `READS AS`, which is itself a heuristic and misclassifies numeric identifiers as measures. `READS AS` is a UI-side inference from distinct counts, null rates and ranges — identifier, free text, measure, code list, date. It is derived for display and does not come back from any tool, so do not present it as an IDMC output.

Beneath the table, the callout that makes the case for the step's position:

> **Why this runs before taxonomy.** "Reads as" is derived from distinct counts, null rates and ranges. Without it the next step classifies on column names alone, which produces descriptions rather than a taxonomy.

Label the four substep buttons for a steward, not for a developer: Warehouse query, Informatica profile, Run profile, Fetch results — tool name in the smaller mono type beside each, as the substep row already does.

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

**Template binding verified end-to-end (25 Jul).** `generate_dq_mapping_task` was run against `M_DQ_Generic` (id `bIWvvmaXtCXbgPPpPRFE4o`) for `CUSTOMER_POSITIONS`, and the resulting IDMC session executed cleanly: parameter override confirmed at runtime (`target object ... overridden with the parameter name = CUSTOMER_POSITIONS_BAD_RECORDS`), zero transformation errors, 19 rows requested / 19 applied / 0 rejected into the target. This closes the highest-consequence untested path — the code binds against the current parameter names (`Src_Object`, `Tgt_Object`, `Input_Field_Map`), not the pre-rename spellings, and the completeness rule runs against real data. The Cloud Data Integration claims are no longer theoretical. Session warnings about optional session attributes (Error Log DB Connection prefix, Truncate Target Table, worklet parameter-file section) are IDMC defaulting unset optionals and did not affect the run.

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

Severity comes from `_classify_severity`: fewer than 5 distinct downstream nodes is LOW, fewer than 20 is MEDIUM, above that is HIGH. A BI-type asset downstream — report, dashboard, metric or KPI — escalates to HIGH **only when distinct nodes also reach 5**; the guard is `if has_bi and distinct_nodes >= SEVERITY_LOW_MAX`. An earlier revision of this document said any BI asset forces HIGH regardless of count. It does not. Render whatever the function returns and do not recompute severity in the UI.

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

## Proposed: Critical Data Element identification

**Not committed, not demo scope.** Recorded because a competitor evaluation exposed the gap concretely and the design should not be reconstructed from memory later.

### Why this exists

Asked to recommend DQ rules against a freshly scanned catalog resource, CLAIRE GPT returned nothing: it identified no critical data elements "based on the provided usage context," and with no CDEs it had no basis for rules. The chain is description → CDE → rules, and the first link failed because the asset had just been scanned and carried no description.

That is the same failure the Opella objection named — classification from names and descriptions rather than from data — one layer higher. This platform profiles before it classifies, so it has evidence available at exactly the point where a description-driven approach has nothing.

Be accurate about the competitor, though. Deriving criticality from *usage context* — which regulatory report or filing consumes this element — is the stronger governance model, not a weaker one. Alation's Critical Data Manager does this deliberately: a Data Consumption represents a business-critical use case, and CDEs are identified relative to it. The limitation is that it requires that context to already be documented. The defensible claim is not that context-driven identification is wrong; it is that this platform can bootstrap criticality from measured evidence when no context exists yet, and absorb context when it arrives.

### Signals, in order of strength

Criticality is scored from what the ladder already produces. The ordering matters: measured signals outrank inferred ones, and that ordering is the argument.

| Signal | Source | Why it ranks here |
|---|---|---|
| **Downstream impact** | `generate_impact_report`, `_classify_severity` on step 2 | Measured, not inferred. A column whose change breaks three BI assets is critical by demonstration. This is the strongest signal available and the one a description-driven approach cannot produce at all. |
| **Semantic class** | step 4 taxonomy, step 6 glossary links | A column classified into a financial or identity domain, or linked to a business term, is business-meaningful by prior steward judgment. |
| **Profile shape** | step 3 | Identifier-like cardinality, monetary ranges, format-regular values. Inference, not measurement — label it as such. |
| **Quality volatility** | step 11, where history exists | A column already failing its rules carries risk. Only available on re-runs; never the primary basis. |

Score to three levels, as Alation does — low, medium, high — with the contributing signals shown per column. **The evidence line is the feature**, exactly as it is on step 4: `← 3 downstream BI assets, classified Financial Exposure, 18/19 distinct`. A criticality score without its derivation is an opinion, and an opinion is what the competitor already offers.

### Placement

A new step between **Curate Columns** and **Recommend Rules**. Both operate at column granularity, so the sequence is natural: link columns to terms, decide which of those columns are critical, then recommend rules for the critical ones.

This changes rule recommendation from "propose rules for all fourteen columns" to "propose rules for the four that matter," which is both better governance and less noise. Alation enforces the same shape — data quality monitors attach only to Control Point elements, not to everything in a CDE.

It is a gate, using `DomainApprovalPanel`: the agent proposes, the steward deselects false positives and approves. Criticality is a judgment with consequences, and it is the class of decision the gates exist for.

Cost to be honest about: inserting a step renumbers 7 through 15 into 8 through 16, and the step count appears throughout this document, the ladder, and the sidebar. The cheaper alternative is folding CDE identification into step 7 as a first stage before rule recommendation, which avoids renumbering but conflates two decisions — what matters, and how we check it — that have different approvers in every mature governance model.

### Where it stitches

A badge that appears only on its own step is decoration. These are the places the designation should actually change behaviour:

- **Step 2, 3** — CDE columns marked in the column metadata and profile tables, so criticality is visible wherever the column appears.
- **Step 7, 8** — rules recommended and created for CDEs first; non-critical columns optional and collapsed.
- **Step 10** — CDE designation published to the catalog alongside terms and rules, so it is visible org-wide rather than living in this tool.
- **Step 11** — monitoring prioritises CDEs, and alert thresholds are tighter for them. A 5-point drop on a critical element should fire where the same drop elsewhere does not.
- **Step 14, 15** — **the strongest stitch.** A dataset containing critical elements requires stricter access control. Dual control already exists on step 15; CDE presence is the natural thing to *trigger* it rather than having it always-on. "This dataset contains 3 critical data elements, so release requires two approvers" is a policy the platform can enforce because it identified the elements itself.

That last one is what makes this a governance capability rather than a labelling feature.

### What not to copy

Alation's CDM carries a standards subsystem — Approval Rules, Baseline Metadata, Risk Assessment Framework and Curation Score standards, each with its own draft/review/published versioning workflow, plus Document Hub ingestion and consumption-unit metering. That is a large surface and most of it is orthogonal to this ladder.

Build the designation and its evidence. Do not build a standards-authoring subsystem to support it; the risk criteria can be configuration until there is a reason for them to be a versioned object.

---

## Where the model is used, and where it deliberately is not

A recurring question, so it lives here rather than being reconstructed from the tool tables each time.

**The dividing line is judgment versus execution.** Every step that reasons — classifies, matches, recommends — uses the LLM. Every step that acts — creates, schedules, publishes, provisions — is deterministic API orchestration with no model in the path. This is not an accident of what got built first; it is the product's argument. Against a freeform chatbot, the differentiator is not *more* AI, it is AI placed at the judgment points with auditable execution and human gates around it. When this agent creates a rule or grants access, that is a real API call, not a model's guess. Adding a model to a step that is currently a clean API call would weaken exactly the claim the comparison document makes.

### Built and using the model today

| Step | Tool | What the model does |
|---|---|---|
| 4 Generate Taxonomy | `generate_governance_taxonomy` | reasons a domain / subdomain / term hierarchy from profiled columns |
| 6 Curate Columns | `suggest_terms_for_asset` | matches columns to existing glossary terms — semantic, not string match |
| 7 Recommend Rules | `recommend_dq_rules` | reasons over the step 3 profile to propose rules per column; no HTTP at all |
| 11 Monitor Quality | `recommend_remediation` | reasons over failing scores to suggest fixes |

Everything else — discover, scan, profile, rule *creation*, scheduling, publishing, delivery, access — is deterministic. The tell in the source is the tool that returns "no HTTP; pure reasoning" versus the one that makes an API call.

### Intended, not yet built

One is already a numbered phase. The other four are candidates, ordered by value-to-risk, and are recorded here so the roadmap has a single reference — none is committed.

**Natural-language rule authoring — Phase 6, committed.** `create_dq_rules` takes a plain-English rule description and builds the spec. This is the strongest form of the "create DQ rule specifications" claim and the one net-new *judgment* use with a phase behind it. It lands with Phase 6; see that section.

**Impact narrative — candidate, highest value, lowest risk.** Step 2's impact panel is a severity count today. A model turning "3 downstream BI assets, HIGH" into "changing this column breaks the Q3 executive dashboard and two regulatory reports" is the kind of sentence that lands in a demo. Low risk because it narrates deterministic output — the severity and the asset list are still computed by `_classify_severity`; the model only describes what is already there. It moves no action behind a model.

**Lineage explanation — candidate, same shape as impact.** The tree shows edges; a model could summarize what the pipeline actually does in a sentence. Same low-risk property: it explains deterministic lineage output rather than producing it.

**Profiling interpretation — candidate, medium value.** `READS AS` is a UI heuristic over distinct counts and null rates today. A model could give a richer semantic read — "this looks like a hashed customer id," "these values are ISO currency codes" — per column. Medium risk: it would sit next to hard statistics, so it must be visibly labelled as inference and never presented as measured, or it undercuts the trust the hard numbers earn.

**Anomaly flagging in monitoring — candidate, medium value, most speculative.** Step 11 plots a score trend. A model could watch the pattern and surface "this degradation matches a schema change" rather than leaving the reader to infer it. Most speculative because it is the one that edges toward prediction rather than description, and prediction is where a model is most likely to be confidently wrong in front of a client.

**Sequencing note.** All four candidates are *narrative-over-deterministic-output* except profiling interpretation and anomaly flagging, which add inference beside hard data. That split is the priority order: impact narrative and lineage explanation are safe additions whenever there is time after the seven phases, because they cannot produce a wrong action, only a wrong sentence about a right one. Profiling interpretation and anomaly flagging need the "this is inference, not measurement" framing built in before they ship. None blocks the demo, and none should displace a phase — they are what comes after 28-of-28, not part of reaching it.

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
5. **Every selector degrades to text, except the step 2 triple.** Dropdowns populate from upstream step state. When the upstream step has not run, the control becomes a free-text input rather than an empty disabled dropdown — this is what makes any step independently runnable, which is what makes the ladder demoable out of order. The database, schema and table controls on step 2 are the deliberate exception: they stay dropdowns because identifier case matters and a typo there produces a "not found" error indistinguishable from a missing grant. They show an unmet-dependency state pointing at step 1 instead.
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
