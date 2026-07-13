---
name: vendor-dd
description: Use when working in this repo (the vendor-dd vendor due-diligence agent) for installing, running the CLI/API/UI, running tests, or navigating the engine/surfaces architecture and conventions.
---

# Working in the vendor-dd repo

A vendor due-diligence agent: give it a vendor name, get a structured, cited risk profile
(snapshot, legal, safety, financial, backlog, certifications, news, and an overall verdict).
Retrieval is [Tavily](https://tavily.com); synthesis is OpenAI's Responses API (structured
outputs). See the root `README.md` for the full walkthrough.

## Setup

Keys live in `.env` at the repo root (loaded by both surfaces; never commit it):

```bash
cp .env.example .env    # set TAVILY_API_KEY and OPENAI_API_KEY (both required)
```

Install the backend package (creates the `vendor-dd` and `vendor-dd-api` commands):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # or: uv venv && uv pip install -e '.[dev]'
```

## Run

```bash
vendor-dd "GE Vernova"           # one-shot report, streamed to the terminal
vendor-dd-api                    # API on :8000 (or: uv run vendor-dd-api)
cd frontend && npm install && npm run dev   # UI on :5173 (needs the API up)
```

Running the CLI/API with keys set **spends real Tavily + OpenAI credits**. Reports are cached
per resolved domain, so re-opening one is free; a genuinely new vendor is not.

## Test / verify

```bash
cd backend  && pytest                  # 186 tests, network-free (Tavily/LLM are faked)
cd frontend && npm test                # 114 tests (vitest)
cd frontend && npx tsc --noEmit        # type-check
```

Prefer running these over asserting something works. Backend logic is built from pure
functions, so most tests need no mocks; add tests test-first.

## Architecture

- `backend/src/vendor_dd/engine/` — surface-agnostic core, **no web framework**. Key modules:
  `config.py` (per-dimension Tavily tuning), `retrieval.py` (multi-query pool + dedup),
  `llm.py` (OpenAI Responses API), `synthesis.py` (results → scored, cited `Section`),
  `pipeline.py` (orchestration; emits events), `cache.py` (per-(domain,dimension) TTL cache),
  `history.py` (append-only score history → trend chart), `schemas.py` (the data model).
- `backend/src/vendor_dd/surfaces/` — thin adapters over the engine: `cli.py` and `api/`
  (FastAPI + SSE, projects/vendors CRUD, dashboard, trend). Both consume the same
  `engine.iter_events` pipeline.
- `backend/src/vendor_dd/logs/` — structured JSON logging (5 channels, correlation ids).
- `frontend/src/` — React + Vite + TS: sidebar, live comparison table (SSE), report panel,
  overview dashboard, score-trend chart.

## Conventions & gotchas

- **Cache and history are keyed by resolved domain** (`vendor_key`), not the typed name, so a
  rename keeps its research and two projects sharing a vendor share lookups.
- **LLM and Tavily sit behind `Protocol` seams** (`LLMClient`, `SearchClient`) with fakes — use
  those in tests; don't hit the network.
- **Scores come from a schema** (findings + citations + score via `responses.parse`), never
  regex-scraped from prose.
- **Never commit** `.env`, `*.db`/`*.sqlite` (the local cache, incl. any seeded demo data), or
  runtime `logs/`. Note: the log ignore is *anchored* (`/logs/`, `backend/logs/`) so it does not
  exclude the `src/vendor_dd/logs/` source package — a bare `logs/` once dropped it from the wheel.
- Follow existing patterns; match the surrounding code's style and test density.
