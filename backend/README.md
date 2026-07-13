# vendor-dd (backend)

The `vendor-dd` Python package: a surface-agnostic due-diligence **engine** (retrieval →
cache → synthesis) consumed by thin **surfaces** (a `vendor-dd` CLI and a `vendor-dd-api`
FastAPI + SSE server). See the [root README](../README.md) for the full project overview,
architecture, and screenshots.

## Install

Standard PEP 517 build (hatchling); installs the `vendor-dd` and `vendor-dd-api` commands.

```bash
cd backend
pip install -e .            # editable, for development
# or
pip install .               # regular install
# or, with uv:
uv pip install -e .
```

Then set your keys (see [`.env.example`](../.env.example) at the repo root):

```bash
export TAVILY_API_KEY=tvly-...   OPENAI_API_KEY=sk-...
```

## Use

```bash
vendor-dd "GE Vernova"       # one-shot report, streamed to the terminal
vendor-dd-api                # serve the API on :8000
```

## Develop

```bash
pip install -e '.[dev]'      # adds pytest
pytest                       # 186 tests
```

## Layout

```
src/vendor_dd/
  db.py            shared SQLite connection setup (WAL, logged exec)
  engine/          retrieval, cache, synthesis, pipeline, schemas — no web framework
  surfaces/        cli.py + api/ (FastAPI, SSE, dashboard, trend)
  logs/            structured JSON logging (5 channels, correlation ids)
  prompts/         entity-resolution + synthesis prompts
tests/             186 tests
```

Build a wheel/sdist with `uv build` (or `python -m build`); artifacts land in `dist/`.
