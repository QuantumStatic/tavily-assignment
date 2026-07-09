# Vendor Due-Diligence Agent

Give it a vendor name, get back a structured, **cited** risk profile a procurement
team could actually file — company snapshot, financial signals, legal/regulatory
red flags, delivery & quality track record, recent news, and an overall risk
summary. Every field carries a source URL and an "as of" date.

Powered by [Tavily](https://tavily.com) search/extract for retrieval, with a
per-section TTL cache, an evaluation loop, and tracing.

> Status: scaffolding. Backend internals are stubbed pending a feasibility spike
> against the Tavily API (see below). Design is being finalized before build.

## Project layout

```
backend/          FastAPI + engine (Python)
  src/vendor_dd/
    engine/       surface-agnostic core: retrieval, cache, synthesis, schemas
    surfaces/     thin adapters: cli, api (SSE), mcp server
  spikes/         feasibility probes (run these first)
  tests/
frontend/         React + Vite + TS (scaffolded after backend feasibility)
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

## Next steps

- [ ] Run feasibility spike, read the output, lock the pipeline shape
- [ ] Finalize + approve the design
- [ ] Build engine (retrieval → cache → synthesis → report)
- [ ] Surfaces: CLI, API (SSE), MCP
- [ ] Eval loop + tracing
- [ ] Frontend
