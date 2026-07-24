# Testing guide — what to test, what's pending

**App:** https://govtest-ui.happytree-b9f21d7d.eastus2.azurecontainerapps.io
**As of:** 2026-07-24, main `0a45bbf`. Demo table throughout: `CUSTOMER_POSITIONS` (schema `DQ_TEST`).

Status legend: ✅ verified live (this build, real org data) · 🔶 wired but not yet fired live · ⏳ blocked on console work.

---

## Before you start

1. **Header check:** "MCP servers 6/6 online" (green). Amber "busy" during a long call is normal; red names dead servers.
2. **Fresh container?** Run step 1 (Discover) once as warm-up — the first run after a deploy takes ~90s and can time out once; re-run if it does.
3. **Testing marketplace (12–15)?** Server-side state dies with each container revision — run steps 1–9 first on the current revision (or the whole ladder via ▶ Run all, approving gates as you go).
4. **↺ New Session** resets browser + server pipeline state.

---

## The 15 steps

### 1 — Discover Catalog ✅
Run Step → collapsible tree of ~12 sources / ~6,000 tables (~90s).
Substeps: `list_connections` → 146 connections table. `scan_mcc_source` → enter `CUSTOMER_POSITIONS` in the card → 14 column chips.

### 2 — Scan Table ✅ (lineage data ⏳)
Pick schema `DQ_TEST` → table `CUSTOMER_POSITIONS` → Run → column chips + timing banner.
Lineage card + substeps (`trace_lineage`, `generate_impact_report`, `find_data_source`): **expect the dual-cause empty state** — that is correct behaviour, not a bug, until the Relationship Discovery console scan runs. Real edges: ⏳.

### 3 — Profile Data ✅ (service mode 🔶)
Table select (defaults to the scanned table) → "Compute locally" → Run → 19 rows, 14 columns of real statistics with the path labelled. Leave and re-enter the step → cached banner ("profiled at …, Run Step to refresh").
🔶 "Informatica service" mode is wired (async job + poll) but hasn't been exercised end-to-end live.

### 4 — Generate Taxonomy ✅
Gated until step 3 has run (hint + jump link — verify the gate by visiting before profiling). Run → ~20s LLM call → 3 domains / 14 terms, **every term with profiling evidence** and the "grounded in the step 3 profile" banner.

### 5 — Domain Structure (Gate 1) ✅
Run → approval panel (~28 items grouped Domain/SubDomain/BusinessTerm). Test rename (click a name), deselect, then Approve & Create → created/skipped/error counts + System & Dataset panel. Re-run: items skip as existing (safe).

### 6 — Curate Columns (Gate 2) ✅ via API, 🔶 first UI click-through
Run → review panel grouped by **match confidence** (expect 14 matches, 0.8–1.0), each row: column, proposed term, basis. Deselect some → Approve & Write Links → only selected links written.
Verified twice through the exact API flow (14/14 links, 0 errors); the UI click-through uses the same panel verified on Gates 1/3/4/5 but deserves one manual pass.
Glossary card: `suggest_terms_for_asset` (→14 suggestions), `detect_glossary_issues` (gaps scope is fastest), `create_glossary_term` 🔶 (writes a real term — use a throwaway name).

### 7 — Recommend Rules (Gate 3) ✅ (rule authoring 🔶)
Gated until step 3. Run → panel grouped by DQ dimension, evidence per row (e.g. "CUSTOMER_ID looks ID-like but has 2 duplicate values") → Approve & Push to Step 8.
Rule authoring card: pick a template → **Validate** (✅ verified: returns valid) → `create_dq_rules` 🔶 (writes a real CDQ rule and joins the selection — fire deliberately once).

### 8 — Create DQ Rules ✅
Context card shows the approved step 7 selection. Run → 4 rules / 14 occurrences. Note the indigo "Scores publish at step 10" banner — the empty CDGC DQ tab here is expected.

### 9 — Schedule Execution ✅ list, 🔶 writes, ⏳ rule binding
Run → lists the org's mapping tasks (100/181) and fills the pickers.
🔶 Not yet fired live (real org writes, most sensible after the template rebuild): `generate_dq_mapping_task`, `create_mapping_task`, `create_schedule` (presets + `.000Z` handled), `create_linear_taskflow`, `run_task` → live job-runs panel.
⏳ Per-task **rule** binding needs the 7-param `M_DQ_Generic` rebuild + the binder follow-up.

### 10 — Publish to Catalog ✅
Run → MCC scan submitted (job id) — or `not_triggered` if a scan ran recently (in-band note, not a failure). `upload_dq_scores` substep → 14 pushed. `propagate_dq_score` form → corrective single-asset push (also the score-history backfill tool).

### 11 — Monitor Quality ✅
Entered cold: type `CUSTOMER_POSITIONS` → Run → composite 98.7%, 4 dimensions. Substeps: trends (classified vs CDGC's own deltas), remediation (healthy), alert → registers with the "stored by this platform, not CDGC" note (indigo).

### 12–14 — Marketplace ✅ (needs current-revision state)
12 → four rows: category **created**, data asset **found**, collection **created**, link **saved_locally** (amber = CSV fallback, import via CDMP UI — known cosmetic). 13 → **PUBLISHED**. 14 → all four delivery tools succeed.
If `data_asset: not_found` → you're on a fresh revision; run steps 1–9 first.

### 15 — Consumer Access (Gates 4/5) ✅
Run → order placed, PENDING. `approve_consumer_order` → **Gate 4** confirmation naming requester/asset/target → approve → FULFILLED. `verify` → COMPLETE. `withdraw` → **Gate 5**, red, names the consumer → WITHDRAWN. (Full lifecycle proven on ORD-8.)

### ▶ Run all (header) ✅
Fresh session → click → watch the strip track position; it **halts** at the first gate or unmet precondition with the fix named, and resumes (skipping done steps) when pressed again. Never auto-approves.

### ⚙ Settings ✅ UI, 🔶 execution
Export (object ids → poll → download ZIP) and Import — wired, not yet fired live (creates real org export jobs).

### Cross-cutting
- Busy/amber header state during Discover: ✅.
- Session-expiry banner: 🔶 — interceptor is code-verified; its first real trigger will be an actual overnight expiry (or a 401-shaped failure).

---

## Pending — consolidated

| # | Item | Owner | Unblocks |
|---|---|---|---|
| 1 | `M_DQ_Generic` 7-param rebuild (Mapping Designer, ~1h; 15-min enumeration gate first) — see IDMC_Console_Runbook.md | console | step 9 rule binding; preflight CHECK |
| 2 | Relationship Discovery scan on the catalog source | console | step 2 lineage with real edges; preflight FAIL |
| 3 | Binder `extra_parameters` + `.env` template-id swap + live task test | me, after #1 | Phase 2 fully done |
| 4 | First deliberate live fire of: step 9 writes, `create_dq_rules` button, export/import, profile service mode, Gate 2 UI click-through | any tester | closes the 🔶 items |
| 5 | Link-asset CSV upload falls back to `saved_locally` | investigate when convenient | cosmetic (amber badge is honest) |
| 6 | Real session-expiry exercise of the reconnect banner | happens naturally overnight | confidence in req-1 |

Pre-demo, always: `bash preflight.sh` with the actual `DEMO_TABLE`, warm-up Discover, and a steps-1–9 run on the current container revision if marketplace will be shown.
