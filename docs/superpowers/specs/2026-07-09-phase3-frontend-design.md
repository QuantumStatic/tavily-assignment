# Vendor Due-Diligence — Phase 3: React Frontend — Design

**Status:** approved design, pending spec review.
**Date:** 2026-07-09
**Depends on:** Phase 2 API (`docs/superpowers/specs/2026-07-09-phase2-api-persistence-design.md`), running at `http://localhost:8000`.

---

## 1. Purpose

A desktop web UI that makes the Phase 2 backend usable without curl/CLI: a procurement user
creates a **project**, adds **vendors** by name, watches each vendor's due-diligence report
**stream in live** (per-dimension scores filling a comparison table as SSE events arrive), and
clicks any vendor to read the **full cited report** in a docked side panel.

Non-goals (deferred): chat-over-citations, auth, multi-user, URL routing, mobile layout.
This is a single-view, desktop-only, single-user demo surface over the existing API.

## 2. Stack

- **React + Vite + TypeScript.** Vite for JSX/TS transpile + dev server; Vitest + React Testing
  Library for tests. Plain hand-written CSS (no UI library, no Tailwind).
- **No router.** One `App` view. State held in React (`useState`/`useReducer`), no Redux/query lib.
- Lives in `frontend/` at the repo root (already scaffolded empty).
- Talks to the backend over two channels, both already built in Phase 2:
  - **REST** via `fetch` — projects/vendors CRUD + read model.
  - **SSE** via native `EventSource` — live report generation (the stream endpoint is a `GET`,
    so native `EventSource` works with no polyfill).
- Backend already emits CORS for `http://localhost:5173` / `http://127.0.0.1:5173` (Vite's default
  dev port), so no proxy is required. The API base URL is read from `import.meta.env.VITE_API_BASE`
  (default `http://localhost:8000`).

## 3. Layout

Three regions, matching the approved mockup (chat-app shape):

```
┌────────────┬─────────────────────────────────────────┬──────────────┐
│  SIDEBAR   │  MAIN                                    │  SIDE PANEL  │
│            │                                          │  (on click)  │
│ Projects   │  <Project name>        [+ Add vendor]    │              │
│  • Bridge  │  ┌────────────────────────────────────┐  │ Cives Steel  │
│    job     │  │ Vendor │Verdict│Legal│Fin│…│News-  │  │ 7/10         │
│  • HVAC Q3 │  │────────┼───────┼─────┼───┼─┼──────-│  │ ──────────   │
│  • …       │  │ Cives  │ 7/10  │  8  │ 6 │…│  7    │  │ Legal 8/10   │
│            │  │ Acme   │ ⣿…    │  5  │ ⣿ │…│  ⣿    │  │  · claim …   │
│ [+ New]    │  │ Boeing │ 3/10  │  2  │ 8 │…│  3    │  │    ↗ url     │
│            │  └────────────────────────────────────┘  │  [Close]     │
└────────────┴─────────────────────────────────────────┴──────────────┘
```

- **Sidebar** (fixed left, ~220px): list of projects; the active one is highlighted. A "New project"
  inline input at the bottom creates a project.
- **Main** (flex center): the active project's header (name + "Add vendor" button) and the vendor
  **comparison table** — one row per vendor, one column per report dimension, plus a leading Vendor
  column and a Verdict column.
- **Side panel** (docked right, ~360px, appears only when a row is selected): the full report for that
  vendor — verdict, then each dimension's reasoning + findings (claim, source URL, source type,
  "as of" date). A close button dismisses it. The table stays visible and shrinks to make room.

## 4. Dimensions (columns)

The report has 7 scored dimensions (from the backend `Dimension` enum, excluding `snapshot` which is
entity resolution, not a scored section):

`legal`, `safety`, `financial`, `backlog`, `certifications`, `news_positive`, `news_negative`

Column order and human labels are defined once in a `DIMENSIONS` constant in the frontend
(e.g. `news_positive` → "News +", `news_negative` → "News −", `backlog` → "Backlog/Ops"). The
table renders a column per entry in that constant, so adding a backend dimension later is a
one-line frontend change.

## 5. Data contracts (consumed as-is from Phase 2)

The frontend does not define its own backend types beyond mirroring these response shapes in
`types.ts`:

- `GET /projects` → `ProjectOut[]` = `{id, name, created_at}[]`
- `POST /projects {name}` → `ProjectOut`
- `GET /projects/{id}` → `ProjectDetail` = `{id, name, created_at, vendors: VendorSummary[]}`
  where `VendorSummary = {vendor_id, name, vendor_key, generated, verdict_score, verdict_reasoning,
  dimensions: {dimension, score, as_of}[]}`
- `POST /projects/{id}/vendors {name}` → `VendorOut` = `{id, project_id, name, vendor_key, created_at}`
- `DELETE /vendors/{id}` → `{ok: true}` (404 if missing)
- `GET /vendors/{id}/report` → `VendorReport` = `{generated, vendor_key, entity, verdict_score,
  verdict_reasoning, sections: Section[]}` where `Section = {dimension, findings, reasoning, score}`
  and `Finding = {claim, citation: {url, title, source_type, score, as_of}}`
- `GET /vendors/{id}/report/stream` → SSE stream. Frame `event:` names and their `data` JSON payloads:
  - `entity_resolved` → `{type, entity: EntityCard}`
  - `section_complete` → `{type, section: Section, cached: bool}`
  - `section_error` → `{type, dimension, message}`
  - `report_complete` → `{type, report: Report}`
  - `report_error` → `{type, message}`

## 6. Data flow

**On load / project switch.** `GET /projects` populates the sidebar. Selecting a project calls
`GET /projects/{id}`; the table renders each vendor's `VendorSummary`. A vendor with
`generated=false` shows an empty row (all dimension cells blank/idle); `generated=true` shows its
cached per-dimension scores and verdict. This is the persisted read model — no stream is running.

**Add vendor (kicks off generation).**
1. `POST /projects/{id}/vendors {name}` → returns the new `vendor_id`; a row appears immediately in a
   "queued" state.
2. The frontend opens `new EventSource(`${API_BASE}/vendors/${vendor_id}/report/stream`)`.
3. As events arrive, the row updates cell-by-cell:
   - `entity_resolved` → row shows the resolved entity name/label; all dimension cells flip to
     "pending" (pulsing).
   - `section_complete` → that `section.dimension` cell shows a score pill (colored by band). The
     Verdict cell stays "pending" until `report_complete` — the backend computes the verdict over all
     sections and only sends it in the terminal `report_complete` event, so the frontend never
     synthesizes a partial verdict itself.
   - `section_error` → that dimension cell shows a small red "failed" marker; generation continues.
   - `report_complete` → the Verdict cell fills with `report.verdict_score`; the stream is closed
     (`EventSource.close()`); the row is final.
   - `report_error` → the row shows a failed badge; the stream is closed; the message is available
     in the side panel if opened.

**Click a row (view report).** Opens the side panel.
- If the vendor is `generated` (or just finished streaming): `GET /vendors/{id}/report` → render verdict
  + sections with findings/citations.
- If the vendor is mid-stream: the panel renders the sections accumulated so far from the live event
  state (no separate fetch needed); it fills in as more `section_complete` events arrive.

**Delete a vendor.** `DELETE /vendors/{id}` → remove the row (and close its stream if active).

## 7. Components

- `App` — owns top-level state (projects, activeProjectId, vendor rows keyed by id, selectedVendorId)
  and orchestrates fetches. A `useReducer` holds the vendor-row map so streamed events reduce cleanly.
- `Sidebar` — project list + `NewProjectForm`.
- `VendorTable` / `VendorRow` — the comparison grid; a row renders one `DimensionCell` per dimension
  (idle / pending / scored / failed states).
- `AddVendorForm` — name input + submit; triggers the POST-then-stream flow.
- `ReportPanel` — the docked side panel; renders `entity`, verdict, and per-dimension findings with
  clickable citation links (open in a new tab; `rel="noopener noreferrer"`).
- `useReportStream(vendorId, onEvent)` — a hook that wraps `EventSource`: opens on demand, parses each
  frame's `event`/`data`, dispatches typed events to the reducer, and closes on `report_complete` /
  `report_error` / unmount. Owns all `EventSource` lifecycle so components never touch it directly.
- `api.ts` — typed `fetch` wrappers for the REST endpoints. `types.ts` — TS mirrors of the response
  shapes in §5.

## 8. Error & edge handling

- `section_error` → failed dimension cell (red marker + tooltip with the message); report still
  completes over the remaining dimensions.
- `report_error` → row failed badge; panel surfaces `message`.
- **EventSource connection drop** mid-stream (network blip, backend restart): the native `EventSource`
  auto-reconnects by default, which would restart the generator and re-spend credits — so on `onerror`
  the hook **closes** the stream instead (no auto-retry) and the row shows a "Regenerate" affordance
  that re-opens the stream on demand. This keeps credit spend user-initiated.
- **Reload during generation:** the in-memory stream state is lost, but the backend cached whatever
  sections completed; the read model (`GET /projects/{id}`) shows the cached partial. The user can
  click "Regenerate" to re-run (which re-streams and re-caches).
- **Backend unreachable:** fetches surface a non-blocking error banner; the app stays usable once the
  backend returns.
- Empty states: no projects → sidebar prompts "Create a project"; project with no vendors → table
  shows an "Add your first vendor" placeholder.

## 9. Testing

Vitest + React Testing Library, fully mocked — **no backend, no API credits**.

- **`fetch` mocked** (per test): table renders from a `ProjectDetail` fixture; a `generated=false`
  vendor renders idle cells; a `generated=true` vendor renders cached scores + verdict.
- **`EventSource` mocked** (a small fake that lets tests push frames): add-vendor opens a stream and
  the row's cells fill in as `entity_resolved` → `section_complete`×N → `report_complete` are pushed;
  a pushed `section_error` renders a failed cell while the rest still complete; `report_complete`
  closes the stream (assert `close()` called).
- **Interaction:** clicking a row opens `ReportPanel` and renders findings + citation links; close
  dismisses it. Creating a project via the sidebar form adds it and selects it.
- **`useReportStream` unit:** `onerror` closes without reopening (credit-safety guarantee);
  unmount closes the stream.

A short manual smoke (run the real backend + `npm run dev`, add a real vendor) is documented in the
plan as **user-triggered only** — it spends Tavily/Nebius credits and must never run in CI.

## 10. File structure

```
frontend/
  package.json
  vite.config.ts            # + vitest config (jsdom env)
  tsconfig.json
  index.html
  .env.example              # VITE_API_BASE=http://localhost:8000
  src/
    main.tsx                # React root
    App.tsx                 # top-level state + orchestration
    types.ts                # TS mirrors of API response shapes (§5)
    api.ts                  # typed fetch wrappers
    dimensions.ts           # DIMENSIONS constant (order + labels)
    hooks/useReportStream.ts
    components/
      Sidebar.tsx
      NewProjectForm.tsx
      VendorTable.tsx
      VendorRow.tsx
      DimensionCell.tsx
      AddVendorForm.tsx
      ReportPanel.tsx
    styles.css
    test/
      setup.ts              # RTL + jsdom setup
      fakeEventSource.ts    # controllable EventSource mock
      *.test.tsx
  README.md                 # run instructions (dev server, env, test)
```

## 11. Out of scope / future

- Chat-over-cached-`sources` (Phase 2 laid down the `sources` column for this; deferred by explicit
  scope decision).
- Auth, multi-user, URL routing, mobile/responsive, real-time collaboration.
- Serving the built frontend from FastAPI (dev runs Vite separately; a production build step is a
  later concern).
