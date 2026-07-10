# Vendor Due-Diligence Agent

Give it a vendor name, get back a structured, **cited** risk profile a procurement
team could actually file — company snapshot, financial signals, legal/regulatory
red flags, delivery & quality track record, recent news, and an overall risk
summary. Every field carries a source URL and an "as of" date.

Powered by [Tavily](https://tavily.com) search/extract for retrieval, with a
per-section TTL cache, an evaluation loop, and tracing.

> Status: backend complete (engine + CLI + FastAPI/SSE API, 70 tests) and a React frontend
> (projects, live streaming comparison table, cited report panel, 36 tests). Runs end-to-end
> locally.

## Project layout

```
backend/          FastAPI + engine (Python)
  src/vendor_dd/
    engine/       surface-agnostic core: retrieval, cache, synthesis, schemas
    surfaces/     thin adapters: cli, api (SSE), mcp server
  spikes/         feasibility probes (run these first)
  tests/
frontend/         React + Vite + TS — sidebar + live comparison table + report panel
evals/            ground-truth sets + eval runner
```

## Setup

1. **Keys.** Copy the template and fill in real values (never commit `.env`):
   ```bash
   cp .env.example .env
   # then edit .env: TAVILY_API_KEY (https://app.tavily.com),
   #                 NEBIUS_API_KEY (https://tokenfactory.nebius.com)
   ```

2. **Feasibility spike — do this first.** Confirms Tavily returns usable material
   before we build on it:
   ```bash
   cd backend/spikes
   uv run tavily_probe.py "Boeing"                     # big public company
   uv run tavily_probe.py "Acme Regional HVAC Supply"  # obscure private vendor
   ```

## Roadmap

- [x] Engine: retrieval → cache → synthesis → report (Phase 1)
- [x] Surfaces: CLI + FastAPI API with SSE streaming + persistence (Phase 2)
- [x] Frontend: projects, live comparison table, cited report panel (Phase 3)
- [x] Tavily per-project `session_id` for search grouping/traceability (Phase 3)
- [ ] **Phase 4 — Tracing & logging:** structured request/run logging across the engine and API,
      and end-to-end tracing (spans per report run / per dimension), aligning with
      industry-standard observability. *(next)*
- [ ] Phase 5 — Eval loop over `evals/ground_truth` (citation-support + contamination checks)
- [ ] Phase 6 — MCP server exposing `check_vendor(name)`; chat over cached `sources`
