# Vendor Due-Diligence — Frontend

React + Vite + TypeScript desktop UI over the backend API: a sidebar of projects, a live
vendor comparison table (fills in over SSE as each dimension completes), a click-to-open
report side panel with cited findings, and an Overview with a portfolio dashboard and a
score-over-time trend chart.

See the [root README](../README.md) for the full picture. This file is dev setup only.

## Run (dev)

The backend must be running first (`cd backend && vendor-dd-api`, serves on :8000 — see the
[backend README](../backend/README.md)).

```bash
cd frontend
cp .env.example .env        # VITE_API_BASE=http://localhost:8000
npm install
npm run dev                 # http://localhost:5173
```

CORS for `localhost:5173` is already configured on the backend.

## Build

```bash
npm run build               # type-checks (tsc -b) then bundles to dist/
npm run preview             # serve the production build locally
```

## Test

```bash
npm test        # 114 tests — Vitest + React Testing Library, fully mocked (no backend, no API credits)
```

## Layout

```
src/
  App.tsx              top-level state, routing between Overview and a project
  api.ts               typed fetch wrapper over the backend
  stream.ts            SSE client for live report generation
  components/          Sidebar, VendorTable/Row, ReportPanel, Dashboard,
                       TrendChart/Panel, MultiSelectDropdown, ProjectFilter, …
  theme.ts             light/dark theme (persisted, prefers-color-scheme aware)
  test/                Vitest + React Testing Library specs
```

## Manual smoke (spends Tavily/OpenAI credits — run only when you mean to)

With the backend up: open the app, create a project, add a real vendor (e.g. "Boeing"), and
watch the row stream in. This spends real Tavily/OpenAI credits per vendor; do not automate it.
