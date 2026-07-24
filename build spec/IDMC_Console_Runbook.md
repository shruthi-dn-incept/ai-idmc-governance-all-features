# IDMC console runbook — checks 2, 4, 5

Three tasks, all in the IDMC UI, none doable in code. Do them in this order: check 2 has a fifteen-minute gate that decides whether the rest of the day is a build or an escalation.

Procedure below comes from the last recorded attempt at `M_DQ_Generic` plus the API behaviour the tools depend on. **Exact menu labels vary between IDMC releases** — where the wording differs, the shape of the task is still right. Substitute your demo org's connection, database and schema for the examples.

---

## Check 2 — build `M_DQ_Generic`

Seven of Phase 2's substeps bind to this. There is no code path: IDMC mapping creation runs over stateful GWT-RPC, not REST, and cloning is blocked by immutable checksums on inner bundles.

### Step 0 — the fifteen-minute gate. Do this before anything else.

1. **Data Integration** → **New** → **Mappings** → **Mapping** → Create.
2. Click the **Source** transformation already on the canvas.
3. **Source** tab → pick your Snowflake connection directly (not a parameter yet).
4. Open the **Object** picker.

**Do tables enumerate?**

- **Yes** → close without saving, continue to step 1 below.
- **No** → stop. This is where the last attempt stalled, with JDBC parameters set and the connection test passing. Escalate now; it outranks every other task today. Quick things to rule out first: the Secure Agent is running and shows the Snowflake package; the connection's warehouse, database and schema fields are populated rather than blank; the agent has been restarted since the connection was last edited.

### Step 1 — target table

The target needs every source column plus one extra. In Snowflake:

```sql
-- if it does not already exist
CREATE TABLE <DB>.<SCHEMA>.CUSTOMER_POSITIONS_BAD_RECORDS AS
SELECT * FROM <DB>.<SCHEMA>.CUSTOMER_POSITIONS WHERE 1=0;

ALTER TABLE <DB>.<SCHEMA>.CUSTOMER_POSITIONS_BAD_RECORDS
  ADD COLUMN PRIMARYRULESET VARCHAR(100);
```

`PRIMARYRULESET` carries the rule verdict. Without it the Target field mapping silently drops the rule output and the mapping writes rows with no verdict.

### Step 2 — create the mapping and its parameters

1. **Data Integration** → **New** → **Mappings** → **Mapping**.
2. Name it exactly `M_DQ_Generic`. Save.
3. Open the **Parameters** panel (parameters icon in the designer toolbar).
4. Add seven **input** parameters. **No dollar signs in the names** — IDMC adds the `$…$` wrapper itself, and typing them produces `$$Src_Conn$$`, which will not bind.

| Name | Type | Notes |
|---|---|---|
| `Src_Conn` | Connection — Snowflake Data Cloud | source connection |
| `Src_Object` | Data object | source table |
| `Tgt_Conn` | Connection — Snowflake Data Cloud | target connection |
| `Tgt_Object` | Data object | target table |
| `Rule_Spec` | String | CDQ rule spec FRS ID |
| `Input_Field_Map` | Field mapping — String if unavailable | e.g. `customer_name=Input` |
| `Source_Filter` | String | optional WHERE clause |

Spelling matters. `generate_dq_mapping_task` binds by exact name and a typo fails at task-creation time with an unhelpful error.

### Step 3 — three transformations

Canvas is **Source → Rule Specification → Target**.

**Source**
- Connection → **Parameter** → `Src_Conn`
- Object → **Parameter** → `Src_Object`, default `CUSTOMER_POSITIONS`
- Fields: include all

**Rule Specification** — drag from the left palette
- Rule → **Parameter** → `Rule_Spec`
- Field Mapping → **Parameterized** → `Input_Field_Map`
- Connect **Source → Rule Specification**
- Confirm outputs list all source fields **plus `PrimaryRuleSet`**. If `PrimaryRuleSet` is absent the rule is not wired and the rest will not work.

**Target**
- Connection → **Parameter** → `Tgt_Conn`
- Object → **Parameter** → `Tgt_Object`
- Operation: **Insert**
- Field map: **Automatic** (by name) — this is what matches `PrimaryRuleSet` to `PRIMARYRULESET`
- Connect **Rule Specification → Target**

### Step 4 — validate, save, capture the ID

1. **Validate**. Resolve everything before saving; a mapping that saves with warnings can still fail at bind time.
2. **Save**.
3. Take the mapping ID from the browser URL — the long alphanumeric segment.
4. Put it in `.env`:
   ```
   IDMC_DQ_TEMPLATE_MAPPING_ID=<id>
   ```
5. Re-run `bash preflight.sh`. Check 2 resolves the mapping **and** asserts all seven parameters are present. A mapping that exists but is missing `Input_Field_Map` passes a visual inspection and fails at bind time.

---

## Check 4 — Relationship Discovery

Lineage does not come from the step 10 scan. It comes from the upfront catalog scan, and if Relationship Discovery has never run, step 2's lineage substeps work perfectly and return nothing.

1. **Metadata Command Center** → **Catalog Sources**.
2. Open the source backing your demo tables.
3. Look at the configured **capabilities**. You need **Metadata Extraction** and **Relationship Discovery**. Extraction has run — that is why tables are discoverable — but Relationship Discovery is the one that derives dataflow.
4. If Relationship Discovery is not enabled, enable it and **Run**. Scans take a while; start it before the other checks.
5. Verify in **Data Governance and Catalog**: search your demo table, open it, check the **Lineage** tab shows upstream or downstream nodes.

**If lineage is still empty after a successful run**, that is the second cause and it is not fixable by scanning again: there are no CDI mappings touching that table for Relationship Discovery to derive dataflow from. Pick a demo table that sits downstream of a real CDI mapping. Confirm with `DEMO_TABLE=<name> bash preflight.sh` — check 4 counts actual lineage edges.

---

## Check 5 — DQ score history

`check_score_trends` needs several score points across different dates. One point renders a flat line and reads as a broken chart.

1. **Data Governance and Catalog** → find your demo table → **Data Quality** tab.
2. Count distinct score dates.

**Three or more** → done.

**Fewer** → backfill. `propagate_dq_score` takes `run_date`, which is exactly what this needs, and it is already wired as a route from Phase 0. Call it once per day you want on the chart, varying `run_date` and `score`, with a mild downward trend so the alert threshold and the degradation story have something to sit on:

```
propagate_dq_score(
  asset_name = "<DEMO_TABLE>",
  score      = 91,
  run_date   = "2026-07-17",
  dimension  = "Completeness",
  passed_rows = 182, failed_rows = 18, total_rows = 200
)
```

Then repeat for 07-18 through 07-23 with scores stepping down toward the low 60s. Six calls gives a trend line, a visible crossing of the 82 threshold, and a remediation story that matches what step 11 renders.

Two things to know: CDGC score rollup is not instant, so leave time between backfilling and demoing; and be straight internally that these are backfilled, because a governance tool showing invented numbers is the one thing that would undercut the argument the product is making.

---

## When all three are done

Re-run `bash preflight.sh` with `DEMO_TABLE` set to whatever is actually being demoed. Checks 2, 4 and 5 all key off it, and passing against a different table tells you nothing.
