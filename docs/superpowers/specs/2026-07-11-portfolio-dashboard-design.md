# Portfolio Overview Dashboard — Design

**Date:** 2026-07-11
**Status:** Approved pending user review

## Goal

A global, cross-project dashboard ("Overview") that turns cached research into decisions:
who to shortlist, who to drop, who's been trusted before — plus calibration charts that
reveal scoring bias in the system itself. Powered by one new read-only endpoint, a
"mark as chosen" flag on vendors, and an append-only score-history table.

## What it shows

The Overview is the landing view (shown when no project is selected) and reachable
anytime via an "Overview" entry pinned at the top of the sidebar. The app no longer
auto-selects the first project on load.

Layout, top to bottom:

### 1. Project filter (dropdown)

A trigger button reading `Projects · all (3)` (or `· 2 of 3` for a subset) opens a
dropdown panel:

- **Search input on top** — filters the option rows with the same normalize-and-includes
  matching as the sidebar search. No create affordance.
- **Select all / Deselect all** action links.
- **Checkbox rows** — one per project, each showing its vendor count on the right.
  Clicking toggles. Panel gets `max-height` + internal scroll for many projects.
- **All projects selected initially.** Every number and chart on the page recomputes
  from the selected set. Deselecting everything shows an empty state
  ("Select at least one project") instead of a zeroed dashboard.

The sidebar's inline filter logic is extracted into a shared helper
(`filterByName(items, query)`: trim, lowercase, `includes`) used by both the sidebar
and this dropdown so matching behavior can never drift.

### 2. Stat cards (4)

| Card | Content | Source |
|---|---|---|
| Portfolio verdict | avg verdict `6.4 / 10` + risk pill (good/mid/bad), "avg across N researched vendors" | verdicts of researched vendors in selected projects |
| Researched | `12 of 14` + "2 reports pending" | generated vs total |
| Risk triage | segmented bar + legend: high risk (verdict ≤3) / watch (4–6) / cleared (≥7) counts | verdict banding |
| Evidence sources | split bar + legend: % independent vs % self-reported | `source_type` across all citations in all sections |

Risk bands: **high ≤3, watch 4–6, cleared ≥7** (consistent with the existing pill
thresholds in the vendor table).

### 3. Decisions band (3 panels)

- **Shortlist** — top 3 vendors by verdict across selected projects, each row: rank,
  name, project name, verdict score. Click navigates to that vendor's report (select
  project, open panel).
- **Red flags** — named dealbreakers, two rules:
  1. any single dimension score ≤3 → row shows `name — Dimension — score`
  2. delivery-risk combo: backlog ≤4 AND financial ≤4 together (neither alone
     triggers) → row shows `name — Backlog X · Financial Y — delivery risk`
- **Most trusted** — vendors ranked by times-chosen (see §Chosen below), row:
  `name — chosen N× · in M projects — current verdict`. Identity is `vendor_key`
  (domain), so "Voith" and "Voith Hydro" aggregate as one vendor.

### 4. Bias charts

- **Verdict distribution** — histogram, one column per score 0–10, columns colored by
  risk band, count label above each bar. Caption: clustering = scoring isn't
  discriminating.
- **Dimension averages** — six horizontal bars (legal, safety, financial, backlog,
  certifications, news) with portfolio average per axis; the weakest axis highlighted
  in danger color with a footnote ("Safety is the softest axis — 3 vendors score ≤3
  on it").
- **Per-dimension mini-histograms** — 3×2 grid of small multiples, one 0–10 histogram
  per dimension, neutral gray bars, only 0/10 edge labels, avg in the corner. Weakest
  dimension gets danger border + bars. These reveal per-axis bias (e.g. certifications
  always piling at 8–9 = non-discriminating dimension).

### 5. Delta markers (from score history)

Where a vendor has a score recorded on an earlier date, decision rows show a delta:
`▼ was 6` / `▲ was 3`. The report panel dimension list shows
`Safety 4 (▼2 since 2026-05-12)`. Deltas appear only once history has ≥2 distinct
dates for that (vendor, dimension); first runs just seed the baseline.

## Mark as chosen

- New toggle control on each vendor row in the project table (next to rename/delete):
  "mark as chosen". **Multiple vendors can be chosen per project** (a project hires
  many trades).
- Stored as `chosen INTEGER NOT NULL DEFAULT 0` on the `vendors` table.
- Chosen rows get a visible marker in the table.
- API: `PATCH /vendors/{id}/chosen` body `{"chosen": true|false}` → updated VendorOut.
- Report panel header shows cross-project stats for the vendor's domain:
  `In 4 projects · chosen 3 times`.

## Score history table

Append-only institutional memory of every score ever assigned. Survives vendor
deletion and cache eviction (deliberately NOT part of `_clear_cache`).

```sql
CREATE TABLE IF NOT EXISTS score_history (
  vendor_key  TEXT NOT NULL,
  dimension   TEXT NOT NULL CHECK (dimension IN (<Dimension values except snapshot>, 'verdict')),
  score       INTEGER NOT NULL CHECK (score BETWEEN 0 AND 10),
  recorded_on TEXT NOT NULL,          -- ISO date, day granularity
  PRIMARY KEY (vendor_key, dimension, recorded_on)
)
```

- The `CHECK (dimension IN ...)` list is **generated from the Python `Dimension` enum**
  at table-creation time (single source of truth) plus the literal `'verdict'`.
  `VERDICT` is NOT added to the `Dimension` enum itself — code iterates that enum where
  verdict must not appear (`EXPECTED_SECTIONS`, `_TAVILY_DIMS`).
- **Idempotent writes: `INSERT OR REPLACE`** — regenerating the same vendor+dimension
  on the same day replaces the row (last write wins: the row is "the score as of that
  day's latest research").
- **Written from the application layer, not SQL triggers.** Reasons: (a) the injectable
  clock — tests drive time via injected clocks, a trigger's `date('now')` would be
  untestable and test-seeded caches would pollute history; (b) verdict isn't stored in
  `report_cache` (computed at read time), a trigger has nothing to extract; (c) trigger
  writes bypass the `_exec` structured logging.
- Write points in the pipeline (using the injected clock's date):
  1. each freshly computed section → record `(vendor_key, dim, section.score, today)`
     — cached sections do NOT re-record (their score is from an earlier date and
     already in history)
  2. on `ReportComplete` → record `(vendor_key, 'verdict', verdict_score, today)`
- History accrues from ship date; no backfill.

## API

### `GET /stats?projects=1,3` → `DashboardStats`

`projects` is a comma-separated list of project ids; omitted = all projects.
Read-only aggregation over `vendors` + `report_cache` + `score_history` — never
triggers generation or external calls.

```python
class VendorRef(BaseModel):        # a clickable vendor reference
    vendor_id: int; name: str; project_id: int; project_name: str

class ShortlistEntry(BaseModel):
    ref: VendorRef; verdict: int; previous_verdict: int | None

class RedFlag(BaseModel):
    ref: VendorRef; kind: Literal["dimension", "delivery_risk"]
    dimension: Dimension | None    # for kind="dimension"
    score: int                     # the offending (or min of pair) score
    detail: str                    # e.g. "Backlog 4 · Financial 3"

class TrustedVendor(BaseModel):
    name: str; vendor_key: str; chosen_count: int; project_count: int
    verdict: int | None

class DashboardStats(BaseModel):
    projects_total: int; projects_selected: int
    vendors_total: int; vendors_generated: int
    avg_verdict: float | None
    risk_high: int; risk_watch: int; risk_cleared: int
    independent_sources: int; self_reported_sources: int
    verdict_histogram: list[int]                 # 11 buckets, index = score
    dimension_avgs: dict[Dimension, float]       # researched vendors only
    dimension_histograms: dict[Dimension, list[int]]
    weakest_dimension: Dimension | None
    weakest_low_count: int                       # vendors ≤3 on the weakest axis
    shortlist: list[ShortlistEntry]              # top 3
    red_flags: list[RedFlag]
    most_trusted: list[TrustedVendor]            # top 3 by chosen_count, ties by verdict
```

All banding/histogram/averaging/red-flag math lives in a **pure function**
`compute_dashboard(vendor_rows, sections_by_key, history) -> DashboardStats`
(unit-testable without the DB); the route only gathers inputs.

Duplicate handling: same-domain vendors within the selection count ONCE for
domain-level stats (histograms, averages, trust) using the earliest row as canonical
— mirrors the existing `duplicate_of` logic. `vendors_total` still counts rows.

### `PATCH /vendors/{vendor_id}/chosen`

Body `{"chosen": bool}`. 404 on unknown vendor. Returns VendorOut (gains a
`chosen: bool` field).

### `GET /vendors/{vendor_id}/report` (extended)

`VendorReport` gains `projects_count: int`, `chosen_count: int` (across all projects,
by domain), and per-dimension `previous: {score: int, recorded_on: str} | None` deltas.

## Frontend

- `Dashboard.tsx` — the Overview view; fetches `/stats` on mount and whenever the
  project selection changes.
- `ProjectFilter.tsx` — the dropdown described above.
- `filterByName` helper extracted to `src/filter.ts`; `Sidebar.tsx` refactored to use it
  (behavior unchanged — its existing tests must stay green).
- `Sidebar` gains an "Overview" entry above the search; active when no project selected.
- `App.tsx`: `activeId === null` renders `<Dashboard/>` instead of the current empty
  state; the initial auto-select of the first project is removed.
- `VendorTable` row gains the chosen toggle; `RowState` carries `chosen`.
- Charts are plain CSS bars (no chart library) matching the mockup:
  `scratchpad/dashboard-mockup.html` reviewed 2026-07-11.
- Empty states: no projects at all → "Create a project to begin"; projects but zero
  selected → "Select at least one project"; selected but nothing researched → cards
  show em-dashes and charts show "No researched vendors yet".

## Error handling

- `/stats` with unknown project ids: ignores them (filter semantics), returns stats for
  the valid remainder; all-invalid behaves like an empty selection.
- Malformed `projects` param (non-integer) → 422.
- History write failures must not fail report generation: log and continue (history is
  best-effort memory, the report is the product).
- Frontend `/stats` fetch failure → existing error-banner pattern.

## Testing

- Pure `compute_dashboard`: banding boundaries (3/4, 6/7), duplicate-domain collapse,
  delivery-risk combo triggers (4/4 yes, 5/4 no), weakest-dimension tie behavior
  (lowest avg wins; ties broken by dimension enum order), empty inputs.
- History: REPLACE idempotency (same day overwrites), distinct days append, CHECK
  constraints reject bad dimension/score, cached sections don't re-record, injected
  clock controls `recorded_on`, vendor deletion leaves history intact.
- API: `/stats` filtering by projects param, 422 on garbage, chosen PATCH round-trip,
  report-level delta fields.
- Frontend: filter dropdown (search narrows, select/deselect all, initial all-selected,
  empty-selection state), chosen toggle optimistic update, sidebar Overview entry
  navigation, `filterByName` shared-helper tests, existing sidebar tests stay green.

## Execution strategy: two parallel lanes

The work splits into two lanes with **zero file overlap** (backend/ vs frontend/), so
they run as two concurrent implementer subagents in separate worktrees, merged at the
end. The spec's API contract (`DashboardStats`, the chosen PATCH, the report
extensions) is the interface both lanes build against — the frontend develops and
tests against typed mocks of that contract, exactly as the existing frontend tests
mock `api`.

**Backend lane** (sequential within — shared `routes.py`/`store.py`):
1. `score_history` module: table + CHECK-generated-from-enum + `record_score`
2. Pipeline write hooks (fresh sections + verdict; cached sections don't re-record)
3. `chosen` column + `PATCH /vendors/{id}/chosen`
4. Pure `compute_dashboard` function
5. `GET /stats` route
6. Report-endpoint extensions (trust counts + per-dimension deltas)

**Frontend lane** (sequential within — several tasks touch `App.tsx`):
1. `filterByName` extraction + Sidebar refactor (existing tests stay green)
2. `ProjectFilter` dropdown
3. `Dashboard.tsx` + charts (against mocked `/stats`)
4. Chosen toggle in the vendor table
5. App wiring (Overview landing view, sidebar entry) + ReportPanel trust/deltas

**Integration task** (after both lanes merge): run full suites against the real
endpoint, live browser verification, fix any contract drift.

Splitting finer than two lanes buys nothing: backend sub-tasks contend on
`routes.py`, frontend sub-tasks on `App.tsx`, and merge cost eats the gain.

## Out of scope (explicitly)

- Sparklines / "biggest movers" panels (the history table enables them later).
- History backfill for pre-existing cached reports.
- Country/industry/parent-company concentration stats.
- Any new report-generation behavior — the dashboard is read-only over existing caches.
