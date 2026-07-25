# Layout reference — read this before opening the HTML

`incept_single_ladder_ui.html` is a **flow and layout reference for humans**. It shows step ordering, gate behaviour, the shell, and the intended layout of every step.

## It is not a source of code

It was built from class names rather than from components. Its rendered output only approximates the real theme and is wrong in detail — spacing, weights, borders and colours all drift from what `index.html` actually produces.

**Do not port markup from it. Do not feed it to a code generator.**

`src/idmc_governance/ui/static/index.html` is the source of truth for anything visual: `DiscoverResult`, `SchemaTableList`, `DomainApprovalPanel`, the Scan step input card, the substep row, the theme.

## How to use it

Read it for structure — which panels sit where, what a result card contains, how a table is columned, what the sidebar row shows. Then build that structure from `index.html`'s components.

`docs/UI_Full_Coverage_Build_Spec.md` transcribes every layout under **Step layouts**, including the shell. Where the spec and this file disagree, the mock is right about layout and the spec is right about behaviour and data availability — the spec has been corrected against the repo, the mock has not.

## Two places the mock is knowingly wrong

**Step 1** renders a flat table. That was identified as an error in the original brief; `DiscoverResult`'s collapsed tree replaces it, carrying the mock's four data points and its Select action.

**Its data is tidier than reality.** Lineage shows a clean 3-in/3-out star; the live pipeline returns 21 edges across three hops with every intermediate node named `Command 1`. Profiling shows five columns; live tables have fourteen or more. Layouts must survive real density.

## Detecting a violation

If a class combination appears in `index.html` that exists nowhere else in that file and matches this mock, markup was ported. The theme is fixed: brand `#6366f1`, `dark #4338ca`, `light #818cf8`, the slate palette, `surface` at `#f1f5f9` / `#e2e8f0` / `#cbd5e1`. Anything outside that is a defect.
