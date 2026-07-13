# Vendor Due-Diligence — Frontend

React + Vite + TypeScript desktop UI over the backend API: a sidebar of projects, a live
vendor comparison table (fills in over SSE as each dimension completes), a click-to-open
report side panel with cited findings, and an Overview with a portfolio dashboard and a
score-over-time trend chart.

See the [root README](../README.md) for the full picture. This file is dev setup only.

## Run (dev)

The backend must be running first (`cd backend && uv run vendor-dd-api`, serves on :8000).

```bash
cd frontend
cp .env.example .env        # VITE_API_BASE=http://localhost:8000
npm install
npm run dev                 # http://localhost:5173
```

CORS for `localhost:5173` is already configured on the backend.

## Test

```bash
npm test        # 114 tests — Vitest + React Testing Library, fully mocked (no backend, no API credits)
```

## Manual smoke (spends Tavily/OpenAI credits — run only when you mean to)

With the backend up: open the app, create a project, add a real vendor (e.g. "Boeing"), and
watch the row stream in. This spends real Tavily/OpenAI credits per vendor; do not automate it.
