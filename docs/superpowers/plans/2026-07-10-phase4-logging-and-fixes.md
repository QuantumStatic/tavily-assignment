# Vendor Due-Diligence — Phase 4: Logging + Review-Driven Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a structured multi-file logging system, and fix the real issues surfaced by the frontend design review, frontend code review, and whole-project backend review — all in one phase.

**Architecture:** Part A adds a `vendor_dd.logs` package (5 named stdlib loggers → 5 JSON-line files, a correlation-ID contextvar propagated into engine worker threads, secret redaction) plus a FastAPI request/response middleware, wired at the LLM/Tavily/DB/HTTP/engine seams. Part B fixes frontend correctness bugs + theme/contrast/a11y. Part C hardens the LLM response path, adds a read-model completeness signal, and folds several observability wins into the new logging.

**Tech Stack:** Backend — Python 3.11+, stdlib `logging`, `contextvars`, sqlite3, FastAPI, openai SDK. Frontend — React 18 + Vite + TS, Vitest + RTL.

Spec: `docs/superpowers/specs/2026-07-10-phase4-logging-and-fixes-design.md`

---

## File structure

```
backend/src/vendor_dd/
  logs/                       # NEW package
    __init__.py               # public API re-exports
    formatter.py              # JsonLineFormatter + redact()
    setup.py                  # configure_logging(), named loggers, correlation contextvar
  engine/llm.py               # MODIFY: guard response (C1), recurse null-coerce (C6), llm logging (A2)
  engine/tavily_client.py     # MODIFY: tavily logging (A3)
  engine/cache.py             # MODIFY: _exec() db logging (A4) + WAL/busy_timeout (C3)
  engine/pipeline.py          # MODIFY: general run logs + copy_context submit (A6) + generic client msg (C4)
  engine/retrieval.py         # MODIFY: filter-count logging (C5)
  surfaces/api/store.py       # MODIFY: _exec() db logging (A4) + WAL/busy_timeout (C3)
  surfaces/api/middleware.py  # NEW: http request/response logging (A5)
  surfaces/api/app.py         # MODIFY: configure_logging() + register middleware
  surfaces/api/schemas.py     # MODIFY: sections_present/expected (C2)
  surfaces/api/routes.py      # MODIFY: completeness signal (C2)
  surfaces/cli.py             # MODIFY: configure_logging() + per-run correlation id
backend/tests/
  test_logging.py             # NEW
  test_llm.py, test_cache.py, test_store.py, test_api.py, test_retrieval.py  # MODIFY: add tests
frontend/
  index.html                  # MODIFY: FOUC inline script (B5)
  src/main.tsx                # MODIFY: ErrorBoundary (B4)
  src/components/ErrorBoundary.tsx  # NEW (B4)
  src/App.tsx                 # MODIFY: stream-drop row error (B2), delete ordering (B3), toggle in toolbar (B6), completeness consume
  src/stream.ts               # MODIFY: pass vendorId to onError (B2)
  src/components/ReportPanel.tsx   # MODIFY: verdict band (B1), partial state
  src/components/VendorTable.tsx / VendorRow.tsx  # MODIFY: verdict cell, a11y, overflow
  src/styles.css              # MODIFY: contrast (B7), transitions, verdict-cell, sticky, reduced-motion
  src/types.ts                # MODIFY: sections_present/expected
  src/test/*                  # MODIFY: add tests
logs/                         # git-ignored (created at runtime)
.gitignore                    # MODIFY: + logs/
```

**Runnable milestones:**
- After A7: backend runs with all 5 log files populating; full suite green.
- After Part B: frontend bugs fixed, Vite HMR shows them live; frontend suite green.
- After Part C: backend hardened; full suite green.

**Servers are running live** (backend :8000 via the py-313 venv, frontend :5173). Frontend edits land in the running checkout so Vite HMR shows them. Backend edits require a manual server restart to take effect live (note this; the test suite uses the uv `.venv`). All tests are zero-credit (fakes + tmp dirs).

---

# PART A — Logging system

## Task A1: `vendor_dd.logs` package (formatter, setup, correlation)

**Files:**
- Create: `backend/src/vendor_dd/logs/__init__.py`, `formatter.py`, `setup.py`
- Test: `backend/tests/test_logging.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_logging.py
import json
import logging

from vendor_dd.logs import (
    configure_logging, get_logger, correlation_id_var, set_correlation_id,
)
from vendor_dd.logs.formatter import JsonLineFormatter, redact


def _read_lines(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_formatter_emits_one_json_object_per_line():
    rec = logging.LogRecord("vendor_dd.llm", logging.INFO, __file__, 1,
                            "llm.request", None, None)
    rec.payload = {"model": "m", "prompt": "hi"}
    rec.correlation_id = "abc"
    line = JsonLineFormatter().format(rec)
    obj = json.loads(line)
    assert obj["logger"] == "vendor_dd.llm"
    assert obj["event"] == "llm.request"
    assert obj["correlation_id"] == "abc"
    assert obj["payload"] == {"model": "m", "prompt": "hi"}
    assert obj["level"] == "INFO" and "ts" in obj


def test_redact_scrubs_known_secret_values(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "super-secret-123")
    assert redact({"api_key": "super-secret-123", "q": "hello"}) == {"api_key": "***", "q": "hello"}
    assert redact("prefix super-secret-123 suffix") == "prefix *** suffix"


def test_configure_logging_creates_five_files_and_isolates(tmp_path):
    configure_logging(tmp_path, level="INFO")
    get_logger("db").info("db.query", extra={"payload": {"sql": "SELECT 1"}})
    for name in ("general", "llm", "tavily", "http", "db"):
        assert (tmp_path / f"{name}.log").exists()
    db_lines = _read_lines(tmp_path / "db.log")
    assert any(o["event"] == "db.query" for o in db_lines)
    # isolation: the db record did NOT leak into general.log
    assert _read_lines(tmp_path / "general.log") == []


def test_correlation_id_flows_onto_records(tmp_path):
    configure_logging(tmp_path, level="INFO")
    token = set_correlation_id("corr-42")
    try:
        get_logger("general").info("app.startup", extra={"payload": {}})
    finally:
        correlation_id_var.reset(token)
    lines = _read_lines(tmp_path / "general.log")
    assert lines and lines[-1]["correlation_id"] == "corr-42"


def test_missing_correlation_id_is_null(tmp_path):
    configure_logging(tmp_path, level="INFO")
    get_logger("general").info("x", extra={"payload": {}})
    assert _read_lines(tmp_path / "general.log")[-1]["correlation_id"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.logs`.

- [ ] **Step 3: Implement the package**

`backend/src/vendor_dd/logs/formatter.py`:
```python
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

_SECRET_ENV_VARS = ("NEBIUS_API_KEY", "TAVILY_API_KEY", "OPENAI_API_KEY")
_MASK = "***"


def _secret_values() -> list[str]:
    return [v for name in _SECRET_ENV_VARS if (v := os.getenv(name))]


def redact(value: Any) -> Any:
    """Recursively replace any known-secret env value with '***' (defense-in-depth;
    we already avoid logging auth, this catches accidental inclusion)."""
    secrets = _secret_values()
    if not secrets:
        return value
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, _MASK)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, correlation_id, event, payload."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "event": record.getMessage(),
            "payload": redact(getattr(record, "payload", {})),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)
```

`backend/src/vendor_dd/logs/setup.py`:
```python
from __future__ import annotations

import contextvars
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from vendor_dd.logs.formatter import JsonLineFormatter

# The five channels. Each maps to logger name `vendor_dd.<name>` and file `<name>.log`.
_CHANNELS = ("general", "llm", "tavily", "http", "db")

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vendor_dd_correlation_id", default=None,
)


def set_correlation_id(value: str) -> contextvars.Token:
    return correlation_id_var.set(value)


class _CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True


def get_logger(channel: str) -> logging.Logger:
    """Return the logger for a channel ('general'|'llm'|'tavily'|'http'|'db')."""
    return logging.getLogger(f"vendor_dd.{channel}")


_configured = False


def configure_logging(log_dir: str | Path | None = None, level: str | None = None) -> None:
    """Idempotent: attach a rotating JSON-line file handler per channel. Safe to call
    more than once (handlers are replaced, not duplicated)."""
    global _configured
    directory = Path(log_dir or os.getenv("VENDOR_DD_LOG_DIR", "logs"))
    directory.mkdir(parents=True, exist_ok=True)
    lvl = (level or os.getenv("VENDOR_DD_LOG_LEVEL", "INFO")).upper()

    fmt = JsonLineFormatter()
    corr = _CorrelationFilter()
    for channel in _CHANNELS:
        logger = get_logger(channel)
        logger.setLevel(lvl)
        logger.propagate = False
        for h in list(logger.handlers):
            logger.removeHandler(h)
        handler = RotatingFileHandler(
            directory / f"{channel}.log", maxBytes=10 * 1024 * 1024, backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(fmt)
        handler.addFilter(corr)
        logger.addHandler(handler)
    _configured = True
```

`backend/src/vendor_dd/logs/__init__.py`:
```python
from vendor_dd.logs.setup import (
    configure_logging, correlation_id_var, get_logger, set_correlation_id,
)

__all__ = ["configure_logging", "get_logger", "correlation_id_var", "set_correlation_id"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_logging.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/logs backend/tests/test_logging.py
git commit -m "feat(logs): structured multi-channel logging package (JSON lines, correlation, redaction)"
```

---

## Task A2: LLM call logging (in `llm.py`)

**Files:**
- Modify: `backend/src/vendor_dd/engine/llm.py`
- Test: `backend/tests/test_llm.py` (append)

Note: this task adds *logging* only. Task C1 (response guarding) and C6 (nested coercion) also edit `llm.py` — do A2 first, then C1/C6 build on it.

- [ ] **Step 1: Write the failing test (append to test_llm.py)**

```python
# append to backend/tests/test_llm.py
import json as _json

def test_structured_logs_request_and_response_without_secrets(tmp_path, monkeypatch):
    from vendor_dd.logs import configure_logging
    monkeypatch.setenv("NEBIUS_API_KEY", "secret-key-xyz")
    configure_logging(tmp_path, level="INFO")

    fake_client = _RecordingClient({"dimension": "legal", "findings": [], "reasoning": "ok", "score": 8})
    llm = NebiusLLM(model="test-model", client=fake_client, api_key="secret-key-xyz")
    llm.structured("classify this", Section)

    lines = [_json.loads(l) for l in (tmp_path / "llm.log").read_text().splitlines() if l.strip()]
    events = [o["event"] for o in lines]
    assert "llm.request" in events and "llm.response" in events
    blob = (tmp_path / "llm.log").read_text()
    assert "secret-key-xyz" not in blob   # api key never logged
    req = next(o for o in lines if o["event"] == "llm.request")
    assert req["payload"]["model"] == "test-model"
```

(Reuse the `_RecordingClient` fake already defined in `test_llm.py`. If its `__init__` doesn't accept `api_key`, the `NebiusLLM(client=...)` path ignores `api_key` anyway — pass it only if the signature allows; otherwise drop that kwarg from the test.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_llm.py -k logs -v`
Expected: FAIL — no `llm.request`/`llm.response` lines.

- [ ] **Step 3: Implement**

In `backend/src/vendor_dd/engine/llm.py`, add near the top imports:
```python
import time

from vendor_dd.logs import get_logger

_LOG = get_logger("llm")
```

Rewrite the body of `structured()` to log around the call (keep the existing schema-building and parse logic; wrap with logging + timing):
```python
    def structured(self, prompt: str, schema: type[T]) -> T:
        json_schema = _to_strict_schema(schema.model_json_schema())
        _LOG.info("llm.request", extra={"payload": {
            "model": self._model, "schema": schema.__name__,
            "reasoning_effort": "none", "prompt": prompt,
        }})
        started = time.monotonic()
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": json_schema, "strict": True},
            },
            reasoning_effort="none",
        )
        content = resp.choices[0].message.content
        latency_ms = round((time.monotonic() - started) * 1000)
        _LOG.info("llm.response", extra={"payload": {
            "model": self._model, "schema": schema.__name__,
            "latency_ms": latency_ms, "content": content,
            "usage": getattr(resp, "usage", None) and _usage_dict(resp.usage),
        }})
        data = _coerce_null_strings(json.loads(content), json_schema)
        return schema.model_validate(data)
```

Add a small usage helper near the top-level helpers:
```python
def _usage_dict(usage) -> dict[str, Any] | None:
    try:
        return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens}
    except Exception:
        return None
```

Note: the `resp.choices[0].message.content` extraction is still unguarded here — Task **C1** adds the guarding + a `llm.error` log. Keep this task focused on the happy-path request/response logging.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_llm.py -v` then `cd backend && uv run pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/llm.py backend/tests/test_llm.py
git commit -m "feat(logs): log LLM request/response (no secrets)"
```

---

## Task A3: Tavily search logging (in `tavily_client.py`)

**Files:**
- Modify: `backend/src/vendor_dd/engine/tavily_client.py`
- Test: `backend/tests/test_retrieval.py` (append) or a small new assertion in `test_logging.py`

- [ ] **Step 1: Write the failing test (append to test_retrieval.py)**

```python
# append to backend/tests/test_retrieval.py
def test_tavily_client_logs_request_and_response(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    from vendor_dd.engine.tavily_client import TavilySearchClient
    configure_logging(tmp_path, level="INFO")

    class _FakeInner:
        def search(self, **kwargs):
            return {"results": [{"title": "t", "url": "u", "content": "c", "score": 0.5}]}

    client = TavilySearchClient.__new__(TavilySearchClient)   # bypass real API-key init
    client._client = _FakeInner()
    client.search(query="acme legal", topic="general", max_results=5)

    lines = [json.loads(l) for l in (tmp_path / "tavily.log").read_text().splitlines() if l.strip()]
    events = [o["event"] for o in lines]
    assert "tavily.request" in events and "tavily.response" in events
    resp = next(o for o in lines if o["event"] == "tavily.response")
    assert resp["payload"]["result_count"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_retrieval.py -k tavily_client_logs -v`
Expected: FAIL — no tavily log lines.

- [ ] **Step 3: Implement**

In `backend/src/vendor_dd/engine/tavily_client.py`, add imports + logger:
```python
import time

from vendor_dd.logs import get_logger

_LOG = get_logger("tavily")
```

Wrap `TavilySearchClient.search`:
```python
    def search(self, **kwargs: Any) -> dict[str, Any]:
        _LOG.info("tavily.request", extra={"payload": {k: v for k, v in kwargs.items()}})
        started = time.monotonic()
        try:
            resp = self._client.search(**kwargs)
        except Exception as exc:
            _LOG.error("tavily.error", extra={"payload": {
                "query": kwargs.get("query"), "error": str(exc)}})
            raise
        latency_ms = round((time.monotonic() - started) * 1000)
        results = resp.get("results", []) if isinstance(resp, dict) else []
        _LOG.info("tavily.response", extra={"payload": {
            "query": kwargs.get("query"), "result_count": len(results),
            "latency_ms": latency_ms, "results": results[:10],
        }})
        return resp
```

(The `kwargs` never contain the api key — it's held on the inner client — so no redaction needed beyond the formatter's defense.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_retrieval.py -v` then `uv run pytest -q`
Expected: PASS, suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/tavily_client.py backend/tests/test_retrieval.py
git commit -m "feat(logs): log Tavily search request/response"
```

---

## Task A4: DB logging via `_exec()` (in `cache.py` + `store.py`)

**Files:**
- Modify: `backend/src/vendor_dd/engine/cache.py`, `backend/src/vendor_dd/surfaces/api/store.py`
- Test: `backend/tests/test_cache.py` (append)

- [ ] **Step 1: Write the failing test (append to test_cache.py)**

```python
# append to backend/tests/test_cache.py
def test_cache_logs_sql_queries(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.LEGAL, {"score": 5})
    lines = [json.loads(l) for l in (tmp_path / "logs" / "db.log").read_text().splitlines() if l.strip()]
    assert any(o["event"] == "db.query" and "report_cache" in o["payload"]["sql"] for o in lines)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_cache.py -k logs_sql -v`
Expected: FAIL — no db.log entries.

- [ ] **Step 3: Implement**

In BOTH `cache.py` and `store.py`, add a module logger and route every `self._conn.execute(sql, params)` through a `_exec` helper. In `cache.py`:

Add imports:
```python
from vendor_dd.logs import get_logger

_LOG = get_logger("db")
```

Add the helper method to `SQLiteCache`:
```python
    def _exec(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        _LOG.info("db.query", extra={"payload": {
            "sql": " ".join(sql.split()), "params": list(params),
            "rowcount": cur.rowcount, "lastrowid": cur.lastrowid,
        }})
        return cur
```

Replace every `self._conn.execute(<sql>, <params>)` in `SQLiteCache` (in `__init__`'s CREATE TABLE, `put`, `_row`, `get` via `_row`, `all_sections`) with `self._exec(<sql>, <params>)`. Keep the `.commit()` calls as-is on `self._conn`.

Do the identical change in `store.py` (`Store` gets its own `_LOG = get_logger("db")` and `_exec`, and every `self._conn.execute(...)` in `__init__`, `create_project`, `list_projects`, `get_project`, `add_vendor`, `list_vendors`, `get_vendor`, `remove_vendor`, `set_vendor_key`, plus the `PRAGMA table_info`/`ALTER TABLE` migration lines route through `_exec`).

IMPORTANT: read the CURRENT `store.py` and `cache.py` first and convert every execute call site — miss none, or that query won't be logged. Do NOT route `.commit()` through `_exec`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_cache.py tests/test_store.py -v` then `uv run pytest -q`
Expected: PASS, suite green (existing cache/store tests unaffected — `_exec` returns the same cursor).

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/cache.py backend/src/vendor_dd/surfaces/api/store.py backend/tests/test_cache.py
git commit -m "feat(logs): log raw SQL queries + results via _exec()"
```

---

## Task A5: HTTP request/response middleware (in `surfaces/api/`)

**Files:**
- Create: `backend/src/vendor_dd/surfaces/api/middleware.py`
- Modify: `backend/src/vendor_dd/surfaces/api/app.py` (register middleware)
- Test: `backend/tests/test_api.py` (append)

- [ ] **Step 1: Write the failing test (append to test_api.py)**

```python
# append to backend/tests/test_api.py
def test_http_middleware_logs_request_and_response(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    client = _client(tmp_path)
    client.post("/projects", json={"name": "p"})
    lines = [json.loads(l) for l in (tmp_path / "logs" / "http.log").read_text().splitlines() if l.strip()]
    events = [o["event"] for o in lines]
    assert "http.request" in events and "http.response" in events
    resp = next(o for o in lines if o["event"] == "http.response")
    assert resp["payload"]["status"] == 200 and "latency_ms" in resp["payload"]


def test_http_middleware_does_not_buffer_the_sse_stream(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        "".join(resp.iter_text())
    lines = [json.loads(l) for l in (tmp_path / "logs" / "http.log").read_text().splitlines() if l.strip()]
    stream_resp = [o for o in lines if o["event"] == "http.response" and "/report/stream" in o["payload"]["path"]]
    assert stream_resp and stream_resp[-1]["payload"].get("body") in (None, "<streaming>")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py -k middleware -v`
Expected: FAIL — no http.log.

- [ ] **Step 3: Implement**

`backend/src/vendor_dd/surfaces/api/middleware.py`:
```python
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from vendor_dd.logs import correlation_id_var, get_logger

_LOG = get_logger("http")
_BODY_CAP = 4096


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request + response and sets a per-request correlation id.
    Never buffers a streaming response body (SSE stays untouched)."""

    async def dispatch(self, request: Request, call_next):
        cid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = correlation_id_var.set(cid)
        started = time.monotonic()
        _LOG.info("http.request", extra={"payload": {
            "method": request.method, "path": request.url.path,
            "query": str(request.url.query), "client": request.client.host if request.client else None,
        }})
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            _LOG.error("http.error", extra={"payload": {
                "method": request.method, "path": request.url.path, "error": str(exc)}})
            correlation_id_var.reset(token)
            raise
        latency_ms = round((time.monotonic() - started) * 1000)
        is_stream = response.headers.get("content-type", "").startswith("text/event-stream")
        _LOG.info("http.response", extra={"payload": {
            "method": request.method, "path": request.url.path,
            "status": response.status_code, "latency_ms": latency_ms,
            "body": "<streaming>" if is_stream else None,
        }})
        response.headers["x-request-id"] = cid
        correlation_id_var.reset(token)
        return response
```

Register it in `app.py`'s `create_app` (add the middleware AND call `configure_logging()` in `build_app`). In `create_app`, after `app = FastAPI(...)` and before/after the CORS middleware add:
```python
    from vendor_dd.surfaces.api.middleware import LoggingMiddleware
    app.add_middleware(LoggingMiddleware)
```

Note: `create_app(deps)` is used by tests with injected fakes; the middleware just needs logging configured. Tests call `configure_logging(tmp)` themselves. In `build_app()` add a `configure_logging()` call (Task A6 finalizes app/cli startup wiring; adding it here is fine — it's idempotent).

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_api.py -v` then `uv run pytest -q`
Expected: PASS. (Body capture for non-streaming JSON responses is intentionally left as status/latency only in this task to avoid consuming/replaying the response stream — keep it simple; bodies for small JSON can be a later enhancement.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/middleware.py backend/src/vendor_dd/surfaces/api/app.py backend/tests/test_api.py
git commit -m "feat(logs): HTTP request/response middleware (SSE-safe, correlation id)"
```

---

## Task A6: Engine general logs + correlation propagation + startup wiring

**Files:**
- Modify: `backend/src/vendor_dd/engine/pipeline.py`, `surfaces/api/app.py`, `surfaces/cli.py`
- Test: `backend/tests/test_logging.py` (append — thread propagation)

- [ ] **Step 1: Write the failing test (append to test_logging.py)**

```python
# append to backend/tests/test_logging.py
def test_correlation_id_propagates_into_engine_worker_threads(tmp_path):
    import json
    from datetime import date
    from vendor_dd.engine.pipeline import ReportEngine, Deps
    from vendor_dd.engine.schemas import EntityCard, Section, Dimension
    configure_logging(tmp_path / "logs", level="INFO")

    class Search:
        def search(self, **kwargs):
            return {"results": [{"title": "x", "content": "Cives Steel", "url": "u", "score": 0.8}]}

    class LLM:
        def structured(self, prompt, schema):
            if schema is EntityCard:
                return EntityCard(name="Cives Steel", domain="cives.com", is_public=False)
            return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=8)

    deps = Deps(search=Search(), llm=LLM(), cache_path=tmp_path / "c.db",
                today=date(2026, 7, 8), fetch_transcript=lambda u: (None, None))
    token = set_correlation_id("run-99")
    try:
        ReportEngine(deps, mode="parallel").run_report("Cives Steel")
    finally:
        correlation_id_var.reset(token)

    # tavily logs are emitted from worker threads; they must still carry the id
    tav = [json.loads(l) for l in (tmp_path / "logs" / "tavily.log").read_text().splitlines() if l.strip()]
    assert tav and all(o["correlation_id"] == "run-99" for o in tav)
```

Note: this uses the injected fake `LLM`/`Search` that don't themselves log, so the correlation assertion rides on the **Tavily** logs, which DO fire (via `TavilySearchClient`)... except the fakes bypass `TavilySearchClient`. Adjust: the engine calls `retrieve_dimension(..., search=deps.search)` — the fake `Search` doesn't log. So instead assert against **general** logs the engine itself emits from a worker-thread context, OR make the fake search call the real logger. Simplest: assert the engine's own `report.start`/`report.complete` general logs carry the id (driver thread), AND add a dedicated worker-thread log line in `_compute_section` (`general` "section.computed") to prove propagation. Implement `_compute_section` to emit a `general` "section.computed" log; assert those carry `run-99`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_logging.py -k propagates -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `pipeline.py`, add:
```python
import contextvars

from vendor_dd.logs import get_logger

_LOG = get_logger("general")
```

In `iter_events`, at the start log `report.start` and wrap the terminal events:
```python
        _LOG.info("report.start", extra={"payload": {"vendor": vendor, "max_workers": self._max_workers}})
```
and before `yield ReportComplete(...)`:
```python
        _LOG.info("report.complete", extra={"payload": {"vendor": vendor, "score": score,
                                                         "sections": len(sections)}})
```
and in each `ReportError` path log `report.error` with the message.

Propagate the correlation context into worker threads — change the submit site:
```python
                ctx = contextvars.copy_context()
                pending[pool.submit(ctx.run, self._compute_section, dim, entity)] = dim
```

Add a worker-thread log in `_compute_section` (proves propagation and is genuinely useful):
```python
    def _compute_section(self, dim, entity):
        deps = self._deps
        results = retrieve_dimension(dim, entity, search=self._search, today=deps.today)
        section = synthesize_section(dim, results, llm=deps.llm)
        _LOG.info("section.computed", extra={"payload": {"dimension": dim.value, "score": section.score}})
        return DimensionOutcome(section=section, raw_results=results)
```

In `app.py build_app()` add at the top (after load_dotenv): `from vendor_dd.logs import configure_logging; configure_logging()` and a `get_logger("general").info("app.startup", extra={"payload": {}})`.

In `cli.py main()`, after building deps: set a per-run correlation id and configure logging:
```python
    import uuid as _uuid
    from vendor_dd.logs import configure_logging, set_correlation_id, get_logger
    configure_logging()
    set_correlation_id(_uuid.uuid4().hex)
    get_logger("general").info("cli.run", extra={"payload": {"vendor": vendor}})
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_logging.py tests/test_pipeline.py tests/test_engine.py -v` then `uv run pytest -q`
Expected: PASS — existing engine/pipeline tests unaffected (logging is additive; the `ctx.run` submit wrapping preserves behavior).

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/pipeline.py backend/src/vendor_dd/surfaces/api/app.py backend/src/vendor_dd/surfaces/cli.py backend/tests/test_logging.py
git commit -m "feat(logs): engine run logs + correlation propagation into worker threads + startup wiring"
```

---

## Task A7: gitignore + runnable check

- [ ] **Step 1:** Add `logs/` to the repo root `.gitignore` (if not already ignored). Verify with `git check-ignore logs` (create `logs/` first if needed).

- [ ] **Step 2:** Full suite: `cd backend && uv run pytest -q` — all green.

- [ ] **Step 3:** Commit:
```bash
git add .gitignore
git commit -m "chore(logs): gitignore logs/"
```

---

# PART C — Backend fixes

## Task C1: Guard the LLM response path + typed error + entity retry

**Files:**
- Modify: `backend/src/vendor_dd/engine/llm.py`, `backend/src/vendor_dd/engine/entity.py`
- Test: `backend/tests/test_llm.py` (append)

- [ ] **Step 1: Write the failing tests (append to test_llm.py)**

```python
# append to backend/tests/test_llm.py
import pytest
from vendor_dd.engine.llm import NebiusLLM, LLMError


class _BadClient:
    def __init__(self, resp):
        self._resp = resp
        self.chat = self
    @property
    def completions(self):
        return self
    def create(self, **kwargs):
        return self._resp


def _resp(content):
    msg = type("M", (), {"content": content})()
    choice = type("C", (), {"message": msg})()
    return type("R", (), {"choices": [choice]})()


def test_none_content_raises_clear_llm_error():
    llm = NebiusLLM(model="m", client=_BadClient(_resp(None)))
    with pytest.raises(LLMError):
        llm.structured("p", Section)


def test_empty_choices_raises_clear_llm_error():
    empty = type("R", (), {"choices": []})()
    llm = NebiusLLM(model="m", client=_BadClient(empty))
    with pytest.raises(LLMError):
        llm.structured("p", Section)


def test_malformed_json_raises_clear_llm_error():
    llm = NebiusLLM(model="m", client=_BadClient(_resp("{not json")))
    with pytest.raises(LLMError):
        llm.structured("p", Section)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_llm.py -k llm_error -v`
Expected: FAIL — `ImportError: LLMError` (and current code raises IndexError/TypeError/JSONDecodeError, not LLMError).

- [ ] **Step 3: Implement**

In `llm.py`, add a typed error and guard extraction:
```python
class LLMError(RuntimeError):
    """The model returned an unusable response (empty/none content or invalid JSON)."""
```

Replace the content-extraction + parse in `structured()` (the part after the `create(...)` call and the `llm.response` log) with:
```python
        content = _extract_content(resp)
        latency_ms = round((time.monotonic() - started) * 1000)
        _LOG.info("llm.response", extra={"payload": {
            "model": self._model, "schema": schema.__name__,
            "latency_ms": latency_ms, "content": content,
            "usage": getattr(resp, "usage", None) and _usage_dict(resp.usage),
        }})
        try:
            data = _coerce_null_strings(json.loads(content), json_schema)
            return schema.model_validate(data)
        except (json.JSONDecodeError, ValueError) as exc:
            _LOG.error("llm.error", extra={"payload": {"schema": schema.__name__, "content": content,
                                                        "error": str(exc)}})
            raise LLMError(f"{schema.__name__}: model returned unparseable/invalid JSON") from exc
```

Add the extractor helper:
```python
def _extract_content(resp) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        raise LLMError("model returned no choices")
    content = getattr(choices[0].message, "content", None)
    if not content:
        raise LLMError("model returned empty content")
    return content
```

Add ONE retry for entity resolution specifically. In `entity.py`, wrap the `llm.structured(...)` call with a single retry on `LLMError`:
```python
from vendor_dd.engine.llm import LLMError
...
    try:
        return llm.structured(ENTITY_RESOLUTION_PROMPT.format(name=name, results=results), EntityCard)
    except LLMError:
        # entity resolution is the pipeline's single point of failure — one retry
        return llm.structured(ENTITY_RESOLUTION_PROMPT.format(name=name, results=results), EntityCard)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_llm.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/llm.py backend/src/vendor_dd/engine/entity.py backend/tests/test_llm.py
git commit -m "fix(engine): guard LLM response extraction with typed LLMError + entity-resolution retry"
```

---

## Task C2: Read-model completeness signal

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/schemas.py`, `backend/src/vendor_dd/surfaces/api/routes.py`
- Test: `backend/tests/test_api.py` (append)

- [ ] **Step 1: Write the failing test (append to test_api.py)**

```python
# append to backend/tests/test_api.py
def test_read_model_reports_section_completeness(tmp_path):
    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension, Section
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    # backfill vendor_key + seed just 2 of 7 sections directly into the shared cache
    key = "cives.com"
    client.app.state.store.set_vendor_key(vid, key)
    cache = SQLiteCache(tmp_path / "db.sqlite")
    for dim in (Dimension.LEGAL, Dimension.FINANCIAL):
        cache.put(key, dim, Section(dimension=dim, findings=[], reasoning="x", score=6).model_dump(mode="json"))
    cache.close()

    summ = client.get(f"/projects/{pid}").json()["vendors"][0]
    assert summ["sections_present"] == 2 and summ["sections_expected"] == 7
    report = client.get(f"/vendors/{vid}/report").json()
    assert report["sections_present"] == 2 and report["sections_expected"] == 7
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py -k completeness -v`
Expected: FAIL — `KeyError: sections_present`.

- [ ] **Step 3: Implement**

In `schemas.py`, add to `VendorSummary` and `VendorReport`:
```python
    sections_present: int = 0
    sections_expected: int = 7
```
(7 = the count of scored dimensions, i.e. `len([d for d in Dimension if d is not Dimension.SNAPSHOT])`. Define a module constant `EXPECTED_SECTIONS` in routes.py from that computation rather than hardcoding 7, and pass it in.)

In `routes.py`, compute `EXPECTED_SECTIONS = len([d for d in Dimension if d is not Dimension.SNAPSHOT])` at module level, and set `sections_present=len(parsed)` / `sections_expected=EXPECTED_SECTIONS` in both `_summarize` (present = number of parsed sections) and `get_report`. For the not-generated/empty branches, `sections_present=0`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_api.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/schemas.py backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_api.py
git commit -m "feat(api): read-model section completeness (sections_present/expected)"
```

---

## Task C3: SQLite WAL + busy_timeout

**Files:**
- Modify: `backend/src/vendor_dd/engine/cache.py`, `backend/src/vendor_dd/surfaces/api/store.py`
- Test: `backend/tests/test_cache.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_cache.py
def test_cache_enables_wal_and_busy_timeout(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    mode = cache._conn.execute("PRAGMA journal_mode").fetchone()[0]
    timeout = cache._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert timeout >= 3000
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_cache.py -k wal -v`
Expected: FAIL — journal_mode is `delete`, timeout default.

- [ ] **Step 3: Implement**

In BOTH `cache.py` and `store.py` `__init__`, right after `self._conn = sqlite3.connect(...)`:
```python
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
```
(These two run before the CREATE TABLE. They don't need `_exec` logging but may be routed through it — either is fine.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_cache.py tests/test_store.py -q` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/cache.py backend/src/vendor_dd/surfaces/api/store.py backend/tests/test_cache.py
git commit -m "fix(db): enable WAL + busy_timeout on cache/store connections"
```

---

## Task C4: Log internals, send generic messages to clients

**Files:**
- Modify: `backend/src/vendor_dd/engine/pipeline.py`
- Test: `backend/tests/test_engine.py` (adjust the existing SectionError/ReportError message assertions)

- [ ] **Step 1: Adjust/write the test**

The existing `test_section_error_does_not_abort_report` asserts `SectionError.dimension == FINANCIAL`. Keep that, but the message becomes generic. Add:
```python
# append to backend/tests/test_engine.py
def test_section_error_message_is_generic_but_logged(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    engine = ReportEngine(_deps(tmp_path, llm=OneDimFailsLLM()), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    err = next(e for e in events if e.type == "section_error")
    assert "boom" not in err.message                      # raw exception text not surfaced
    assert err.dimension is Dimension.FINANCIAL
    lines = (tmp_path / "logs" / "general.log").read_text()
    assert "boom" in lines                                # ...but the detail IS logged server-side
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_engine.py -k generic_but_logged -v`
Expected: FAIL — current code puts `str(exc)` ("boom") into the SectionError message.

- [ ] **Step 3: Implement**

In `pipeline.py`, where `SectionError`/`ReportError` are built from `str(exc)`, log the full detail to the `general` logger and put a generic message in the event:
```python
                except Exception as exc:
                    _LOG.error("section.error", extra={"payload": {"dimension": dim.value, "error": str(exc)}})
                    yield SectionError(dimension=dim, message=f"{dim.value} lookup failed")
                    continue
```
and similarly for the two `ReportError` sites (entity + backlog):
```python
            except Exception as exc:
                _LOG.error("report.error", extra={"payload": {"stage": "entity", "error": str(exc)}})
                yield ReportError(message="report could not be generated")
                return
```
(use `stage: "backlog"` for the backlog site).

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_engine.py -v` then `uv run pytest -q`
Expected: PASS. (Update any other test that asserted on the old raw messages, e.g. the entity/backlog `ReportError` message tests — change them to the new generic strings while still asserting the error path fires.)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/pipeline.py backend/tests/test_engine.py
git commit -m "fix(engine): generic client-facing error messages; full detail logged server-side"
```

---

## Task C5: Filter-count observability

**Files:**
- Modify: `backend/src/vendor_dd/engine/retrieval.py` (where `filter_results`/contamination filtering is applied)
- Test: `backend/tests/test_retrieval.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_retrieval.py
def test_retrieval_logs_filtered_count(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    # drive retrieve_dimension with a fake search whose results get filtered by the
    # anti-contamination check (entity name absent), then assert a tavily/general log
    # records kept vs filtered counts. (Adapt to the real retrieval signature.)
    ...
```

Read `retrieval.py` to see exactly where filtering happens and what's available; write a concrete test that seeds results (some mentioning the entity, some not), calls `retrieve_dimension`, and asserts a `retrieval.filtered` log line with `kept`/`filtered` counts.

- [ ] **Step 2–4:** Implement a `get_logger("general").info("retrieval.filtered", extra={"payload": {"dimension": ..., "kept": k, "filtered": n}})` at the filtering site in `retrieval.py`; run tests; confirm green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/retrieval.py backend/tests/test_retrieval.py
git commit -m "feat(logs): record anti-contamination kept/filtered counts per dimension"
```

---

## Task C6: Recurse `_coerce_null_strings`

**Files:**
- Modify: `backend/src/vendor_dd/engine/llm.py`
- Test: `backend/tests/test_llm.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_llm.py
def test_coerce_null_strings_recurses_into_nested_objects():
    from vendor_dd.engine.llm import _coerce_null_strings
    from vendor_dd.engine.schemas import Section
    schema = Section.model_json_schema()
    data = {"dimension": "legal", "reasoning": "x", "score": 5, "findings": [
        {"claim": "c", "citation": {"url": "u", "title": "t", "source_type": "independent",
                                    "score": 0.5, "as_of": "null"}}]}
    out = _coerce_null_strings(data, schema)
    assert out["findings"][0]["citation"]["as_of"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_llm.py -k recurses -v`
Expected: FAIL — nested `"null"` not coerced.

- [ ] **Step 3: Implement**

Rewrite `_coerce_null_strings` to walk the schema recursively (resolve `$ref` into `$defs`, descend objects and array items), coercing the literal string `"null"` → `None` on any nullable field at any depth. Keep the top-level behavior identical. (Reuse the `$defs` resolution pattern from `_nullable_keys`.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && uv run pytest tests/test_llm.py -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/llm.py backend/tests/test_llm.py
git commit -m "fix(engine): coerce literal 'null' strings recursively (nested fields)"
```

---

# PART B — Frontend fixes

All frontend tasks: run `cd frontend && npm test` and `npm run build` after each; edits land in the running checkout (Vite HMR).

## Task B1: Verdict pill color (bug)

**Files:** Modify `frontend/src/components/ReportPanel.tsx`; Test `frontend/src/test/panel.test.tsx`.

- [ ] **Step 1:** Add to `panel.test.tsx` a test rendering a `verdict: { score: 2 }` row and asserting the header pill has class `bad` (not `good`):
```tsx
test('verdict pill uses the score band, not always green', () => {
  render(<ReportPanel row={{ ...row, verdict: { score: 2 } }} onClose={() => {}} />)
  const pill = screen.getByText('2/10')
  expect(pill.className).toMatch(/\bbad\b/)
})
```
- [ ] **Step 2:** Run `npm test -- panel` → FAIL (currently always `good`).
- [ ] **Step 3:** In `ReportPanel.tsx`, import `bandForScore` from `../band` and change the verdict pill to `className={\`pill ${bandForScore(row.verdict.score)}\`}`.
- [ ] **Step 4:** `npm test -- panel` → PASS; `npm test` green.
- [ ] **Step 5:** Commit: `fix(frontend): verdict pill uses score band instead of hardcoded green`.

## Task B2: Stream-drop marks the row failed (bug)

**Files:** Modify `frontend/src/stream.ts` (pass vendorId to onError), `frontend/src/App.tsx`; Test `frontend/src/test/app.test.tsx`.

- [ ] **Step 1:** Add an app test: add a vendor, `FakeEventSource.last().fail()`, assert the row shows a failed state (e.g. a `✗`/`failed` cell or verdict), NOT perpetual `pending`.
- [ ] **Step 2:** Run → FAIL (row stays pending).
- [ ] **Step 3:** Change `openReportStream(vendorId, onEvent, onError)`'s `onError` to be called (it already is) — in `App.tsx addVendor`, change the `onError` handler to dispatch a row-level failure for `v.id`:
```tsx
      () => {
        dispatch({ kind: 'event', vendorId: v.id, ev: { type: 'report_error', message: 'stream dropped' } })
        setError('The report stream dropped — the row is marked failed. Delete and re-add to retry.')
      },
```
(`reduceEvent`'s `report_error` case already sets `status:'error'` + `verdict:'failed'`; the cells stay as-was — optionally also flip pending cells to failed in the reducer's `report_error` case for a cleaner visual.)
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5:** Commit: `fix(frontend): mark row failed when its SSE stream drops`.

## Task B3: Delete-failure keeps a coherent row (bug)

**Files:** Modify `frontend/src/App.tsx`; Test `frontend/src/test/app.test.tsx`.

- [ ] **Step 1:** Extend the existing delete-failure test to assert the row's stream teardown happens only on success (mock DELETE 500 → row present AND not left in a half-torn state).
- [ ] **Step 2:** Run → (may already partially pass) confirm the ordering gap.
- [ ] **Step 3:** In `removeVendor`, move the stream close/delete to AFTER a successful `await api.deleteVendor(vendorId)`:
```tsx
  async function removeVendor(vendorId: number) {
    try {
      await api.deleteVendor(vendorId)
      streams.current.get(vendorId)?.()
      streams.current.delete(vendorId)
      dispatch({ kind: 'remove', vendorId })
      if (selectedVendorId === vendorId) setSelectedVendorId(null)
    } catch {
      setError('Could not delete the vendor.')
    }
  }
```
- [ ] **Step 4:** Run → PASS; full suite green.
- [ ] **Step 5:** Commit: `fix(frontend): tear down vendor stream only after a successful delete`.

## Task B4: Error boundary (crash containment)

**Files:** Create `frontend/src/components/ErrorBoundary.tsx`; Modify `frontend/src/main.tsx`; Test `frontend/src/test/errorboundary.test.tsx`.

- [ ] **Step 1:** Test: render `<ErrorBoundary><Throw/></ErrorBoundary>` where `Throw` throws; assert a fallback message renders (not a blank page).
- [ ] **Step 2:** Run → FAIL (no module).
- [ ] **Step 3:** Implement a class `ErrorBoundary` (getDerivedStateFromError → fallback UI: "Something went wrong — reload the page."). Wrap `<App/>` in `main.tsx` with it inside `StrictMode`.
- [ ] **Step 4:** Run → PASS; full suite green; `npm run build` clean.
- [ ] **Step 5:** Commit: `feat(frontend): error boundary so one bad frame can't white-screen the app`.

## Task B5: Dark-mode FOUC inline script

**Files:** Modify `frontend/index.html`.

- [ ] **Step 1:** In `index.html <head>`, before the module script, add a tiny blocking script:
```html
    <script>
      (function () {
        try {
          var t = localStorage.getItem('vendor-dd-theme');
          if (t !== 'light' && t !== 'dark') {
            t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
          }
          document.documentElement.setAttribute('data-theme', t);
        } catch (e) {}
      })();
    </script>
```
- [ ] **Step 2:** Manual: reload http://localhost:5173 in dark mode — no light flash. `npm run build` clean (this is HTML-only; no unit test).
- [ ] **Step 3:** Commit: `fix(frontend): set data-theme before bundle load to kill dark-mode FOUC`.

## Task B6: Move theme toggle into the toolbar

**Files:** Modify `frontend/src/App.tsx`, `frontend/src/styles.css`; Test `frontend/src/test/app.test.tsx` (toggle still present/works).

- [ ] **Step 1:** Update/keep a test asserting the toggle button renders and toggles regardless of whether the panel is open.
- [ ] **Step 2:** Move `<ThemeToggle/>` from the app root into the toolbar row (right-aligned, alongside AddVendorForm), and add a persistent top-right slot for it even when no project is active (e.g. a small header bar or place it in `.main`'s top-right). Change `.theme-toggle` from `position: fixed` to in-flow within that slot (drop the fixed positioning / z-index).
- [ ] **Step 3:** Manual: open a report panel — toggle no longer overlaps the ✕. `npm test` + `npm run build` green.
- [ ] **Step 4:** Commit: `fix(frontend): relocate theme toggle into the toolbar (no panel overlap)`.

## Task B7: Contrast tokens

**Files:** Modify `frontend/src/styles.css`.

- [ ] **Step 1:** In `:root`, `--text-muted: #888888` → `#6b7280`; `--text-faint: #bbbbbb` → `#8a8a8a`. In `:root[data-theme="dark"]`, `--text-faint: #667079` → `#8b95a1`.
- [ ] **Step 2:** `npm run build` clean; visually verify muted/faint text is readable in both themes.
- [ ] **Step 3:** Commit: `fix(frontend): raise muted/faint text contrast to WCAG AA`.

## Task B8: Consume the completeness signal (C2) in the UI

**Files:** Modify `frontend/src/types.ts` (add `sections_present`/`sections_expected` to `VendorSummary` + `VendorReport`), `frontend/src/rows.ts` (carry completeness on a generated row), `frontend/src/components/ReportPanel.tsx` (show "N of 7 dimensions — still generating" when partial), `frontend/src/App.tsx selectVendor` (don't early-return to a blank panel on partial). Test: `rows.test.ts` + `panel.test.tsx`.

- [ ] **Step 1:** Tests: a generated-but-partial summary (`sections_present:2, sections_expected:7`) surfaces a partial indicator; the panel renders an "incomplete" note rather than a blank body when `entity`/verdict are missing.
- [ ] **Step 2–4:** Implement: add the two fields to the TS types; thread onto `RowState` (e.g. `sectionsPresent`/`sectionsExpected`); in `selectVendor`, when `getReport` returns generated-but-incomplete, dispatch a report with whatever's present and let the panel show a "still generating (N of M)" banner instead of returning early to an empty panel. Run tests → green; `npm run build` clean.
- [ ] **Step 5:** Commit: `feat(frontend): surface report completeness (N of 7) + graceful partial-report panel`.

## Task B9: A11y + polish batch (cheap wins)

**Files:** Modify `frontend/src/App.tsx` (error banner), `frontend/src/styles.css`, `frontend/src/components/DimensionCell.tsx`, `VendorRow.tsx`/`VendorTable.tsx`. Test where meaningful.

Apply, each verified by `npm run build` + a targeted test where it makes sense:
- [ ] Error banner: `role="alert"` + a visible ✕ dismiss button (test: banner has role alert).
- [ ] Pulsing dot: `@media (prefers-reduced-motion: reduce) { .dot { animation: none } }` + an sr-only "pending" label in `DimensionCell` pending state (test: sr-only text present).
- [ ] Verdict column: a `.verdict-cell` class with a subtle left border / tint so it's visually anchored.
- [ ] Table robustness: wrap `<table>` in a `div.table-scroll { overflow-x: auto }`; `.vendor-name { max-width; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }`; `th { position: sticky; top: 0; background: var(--bg) }`.
- [ ] Theme transition: extend the `.2s` transition to `.sidebar`, `.panel`, `.vendor-table td/th` borders (or drop the body transition) so the swap isn't half-animated.
- [ ] Copy: differentiate the two empty states (main vs sidebar); give `.add-vendor button` an accent-filled look (`background: var(--accent); color: white`).

- [ ] **Commit:** `feat(frontend): a11y + table + theme polish (alert banner, reduced-motion, sticky/scroll, verdict cell)`.

---

## Deferred (NOT in this plan)
- `<tr role="button">` table-semantics rework; `TextSubmitForm` / `useVendorStreams` extraction; in-flight stream de-duplication; dead `DIMENSION_CONFIGS` cleanup; per-request non-streaming HTTP body capture; fuzzier anti-contamination matching. (All noted in the spec.)

---

## Self-review notes

- **Spec coverage:** A1–A7 → spec Part A (§A1–A8) ✓; C1–C6 → spec Part C (C1–C6) ✓; B1–B9 → spec Part B (B1–B13) + the B⇄C2 cross-cutting note ✓. Deferred items match the spec's deferred lists.
- **Ordering rationale:** Part A (logging) first because C4/C5 log through it and C1 pairs with A2 (both edit `llm.py`); C after A; B independent (frontend) and can run in parallel/any order after C2 (B8 consumes C2's fields). The plan lists A → C → B; B8 depends on C2 being done.
- **Dependencies called out:** A2 before C1/C6 (all `llm.py`); C2 before B8; A1 before every other logging task.
- **Types/consistency:** `get_logger`/`configure_logging`/`correlation_id_var`/`set_correlation_id`, `_exec`, `LLMError`, `sections_present`/`sections_expected`, `bandForScore`, `ErrorBoundary` used consistently across tasks.
- **No placeholders:** substantive new files have full code; C5's test is intentionally marked "adapt to the real retrieval signature" because `retrieval.py`'s exact filter site must be read first — the implementer reads it and writes the concrete test (this is a read-first instruction, not a TODO).
- **Credit safety:** every test uses fakes + tmp dirs; no live Tavily/Nebius calls. The only credit-spending action remains the user-triggered live demo.
