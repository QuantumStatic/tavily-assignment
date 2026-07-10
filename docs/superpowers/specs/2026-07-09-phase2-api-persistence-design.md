# Phase 2 — API + Persistence + Streaming Engine — Design Spec

**Date:** 2026-07-09
**Context:** Tavily FDE take-home. Phase 1 shipped the engine + CLI (`vendor-dd`). Phase 2 puts a
FastAPI service in front of the engine so the Phase 3 React frontend has an API to drive: project /
vendor persistence, a streaming report endpoint (SSE), and a parallelized engine.
**Author:** Utkarsh Jain
**Phase 1 spec:** `docs/superpowers/specs/2026-07-08-vendor-due-diligence-design.md`

---

## 1. Purpose

Phase 1's engine is synchronous and returns a whole `Report` at the end. Phase 2 adds three things:

1. **A parallelized, streaming engine** — dimensions fan out concurrently (~40s → ~8-10s), and
   sections are emitted as they complete so the UI can fill in live.
2. **Persistence** — projects and vendors, so the frontend's comparison workspace has something to
   store and read (`projects → vendors → cached reports`).
3. **An HTTP API** — REST for project/vendor CRUD + read model, and an SSE endpoint that runs the
   engine and streams results.

The engine stays **surface-agnostic**: it emits typed events; the API and CLI are thin renderers.
Everything is testable with injected fakes, so the whole suite runs on **zero Tavily/Nebius credits**.

---

## 2. Streaming, parallel engine

### 2.1 One engine, a `mode` parameter

A single `ReportEngine` class replaces the free functions in `pipeline.py`. Concurrency is chosen by
a constructor parameter, not by subclassing (composition over inheritance):

```python
class ReportEngine:
    def __init__(self, deps: Deps, *, mode: Literal["parallel", "sequential"] = "parallel",
                 max_workers: int = 6): ...
    def iter_events(self, vendor: str) -> Iterator[ReportEvent]: ...   # streaming
    def run_report(self, vendor: str) -> Report: ...                   # drains iter_events
```

- `mode="parallel"` → `ThreadPoolExecutor(max_workers=max_workers)`.
- `mode="sequential"` → `ThreadPoolExecutor(max_workers=1)`: same code path, but single-worker
  execution makes completion order deterministic (submission order). Used by tests/evals and
  available to the CLI for readable, ordered output. No second class, no inheritance.

Threads (not async) because the work is **I/O-bound** (network waits on Tavily + Nebius), so the GIL
is released during the waits. The existing synchronous `SearchClient` / `LLMClient` protocols are
reused unchanged.

`run_report(vendor, deps)` remains as a **module-level compatibility function** (instantiates a
default `ReportEngine` and calls it) so the Phase 1 CLI and all existing tests keep working.

### 2.2 Algorithm (identical to Phase 1, now concurrent)

`iter_events(vendor)`:
1. Resolve entity (cached under `Dimension.SNAPSHOT`, per Phase 1) → yield `EntityResolved(entity)`.
2. Compute `vendor_key = (entity.domain or entity.name).strip().lower()`.
3. **Driver thread** checks the cache for each of the 6 Tavily dimensions:
   - cache hit → yield `SectionComplete(section, cached=True)` immediately (no pool work).
   - cache miss → submit `_compute_section(dim, entity, today)` to the executor.
4. As futures complete (`as_completed`): the **driver thread** does `cache.put(...)` (with raw
   sources), then yields `SectionComplete(section, cached=False)`. If the dimension was
   `NEWS_POSITIVE`, its raw results are stashed in a run-local variable for backlog reuse.
5. Backlog runs on the driver thread after the fan-out (transcript for public vendors; reuse of the
   run-local `NEWS_POSITIVE` results for private) → yield `SectionComplete(backlog)`.
6. Assemble verdict from all sections → yield `ReportComplete(report)`.

### 2.3 Concurrency rules (what parallelism forces)

- **Workers touch no database.** SQLite connections are not cross-thread safe. `_compute_section`
  (the pool task) does only `retrieve_dimension` + `synthesize_section` and returns a
  `DimensionOutcome(section, raw_results)` — **no cache access**. All `cache.get` / `cache.put` happen
  on the driver thread. This keeps `SQLiteCache` single-threaded and lock-free.
- **Per-run state stays local.** The executor is created inside `iter_events` (context-managed,
  cleaned up per run) and the `NEWS_POSITIVE` raw-results handoff is a local variable — so one engine
  instance can serve concurrent reports safely (matters for the API).

### 2.4 Events

Typed, small, JSON-serializable (Pydantic models with a `type` discriminator), so they map directly
to SSE payloads:

| Event | Payload | Meaning |
|---|---|---|
| `EntityResolved` | `entity: EntityCard` | Entity card resolved (first event) |
| `SectionComplete` | `section: Section`, `cached: bool` | One dimension finished (streams out-of-order in parallel mode) |
| `SectionError` | `dimension: Dimension`, `message: str` | One dimension failed; report continues without it |
| `ReportComplete` | `report: Report` | Verdict assembled (terminal success event) |
| `ReportError` | `message: str` | Fatal error (e.g. entity resolution failed); terminal |

`ReportEvent = Union[...]` discriminated on `type`.

### 2.5 Error handling

- A single dimension's worker raising → caught by the driver → `SectionError` for that dimension; the
  report still completes with the remaining sections (verdict computed over what succeeded).
- A fatal error (entity resolution fails, or the whole run throws) → `ReportError`, stream closes.
- The CLI's renderer gains handling for `SectionError` (prints a note) but otherwise unchanged.

---

## 3. Persistence

One SQLite **file**, shared with the report cache, three tables:

```sql
projects (id INTEGER PK, name TEXT, created_at TEXT)
vendors  (id INTEGER PK, project_id INTEGER FK, name TEXT, vendor_key TEXT NULL, created_at TEXT)
report_cache (vendor_key, section_type, content, sources, fetched_at, PRIMARY KEY (vendor_key, section_type))
```

- **`report_cache` gains a `sources` column** (JSON: the raw Tavily results used for the section).
  Nearly free — the parallel workers already return raw results. Unlocks the Phase 3 chat feature
  (RAG over cached sources) and an audit trail. Nullable, so existing rows/writes stay valid.
- **`vendors.vendor_key` is null until the first report.** A vendor is added by name only; entity
  resolution (which produces the canonical domain) runs at report time, then backfills `vendor_key`.
  The read model joins `vendors.vendor_key → report_cache`.

### 3.1 Store module

`surfaces/api/store.py` — a `Store` class, db-path injected (same pattern as `SQLiteCache`), tables
created on init (`CREATE TABLE IF NOT EXISTS`). Methods:

```
create_project(name) -> Project
list_projects() -> list[Project]
get_project(id) -> Project | None
add_vendor(project_id, name) -> Vendor
list_vendors(project_id) -> list[Vendor]
remove_vendor(vendor_id) -> None
set_vendor_key(vendor_id, vendor_key) -> None      # backfill after first report
get_vendor(vendor_id) -> Vendor | None
```

### 3.2 Cache read method for the table

`SQLiteCache` gains `all_sections(vendor_key) -> dict[Dimension, tuple[dict, datetime]]` — returns
every stored section for a vendor with its `fetched_at`, **ignoring TTL** (the comparison table shows
whatever's cached, with an "as of" badge; freshness is a UI concern, not a reason to hide data).

---

## 4. HTTP API

`surfaces/api/` package:
- `app.py` — two entrypoints:
  - `create_app(deps: Deps, db_path: Path) -> FastAPI` **factory** — the real builder; tests inject
    fake `deps` → zero credits. Adds CORS middleware (configurable origins; default the Vite dev
    origin `http://localhost:5173`), registers routers, wires `sse-starlette`.
  - `build_app() -> FastAPI` — a **zero-arg production entrypoint** that reads keys from `.env`,
    constructs real `Deps` (`TavilySearchClient`, `NebiusLLM`, `fetch_transcript_text`) + the default
    db path, and returns `create_app(deps, db_path)`. This is what `uvicorn --factory` targets.
- `store.py` — persistence (§3.1).
- `routes.py` — endpoint handlers.
- `sse.py` — the sync-generator → async-SSE bridge (§4.2), its own testable unit.
- `schemas.py` — API request/response models (`ProjectIn/Out`, `VendorIn/Out`, `ReportSummary`,
  `VendorReport`), distinct from engine schemas.

### 4.1 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/projects` | Create a project `{name}` |
| `GET` | `/projects` | List projects |
| `GET` | `/projects/{id}` | Project + its vendors + each vendor's **report summary** (per-dimension scores, verdict, "as of"; empty if not generated) — the comparison table's data |
| `POST` | `/projects/{id}/vendors` | Add a vendor by `{name}` (vendor_key null until first report) |
| `DELETE` | `/vendors/{id}` | Remove a vendor |
| `GET` | `/vendors/{id}/report` | Assembled report from cache (sections + recomputed verdict), or an empty state |
| `GET` | `/vendors/{id}/report/stream` | **SSE**: run the engine, stream `EntityResolved → SectionComplete… → ReportComplete`, persisting sections + backfilling `vendor_key` |

- The stream endpoint is a **`GET`** because browsers' `EventSource` only issues GET requests.
  Generation-on-GET is effectively idempotent: cached sections stream back instantly, fresh ones are
  computed once and cached.
- `GET /projects/{id}` recomputes each vendor's verdict from cached sections (verdict is never stored
  independently — Phase 1 rule).

### 4.2 SSE bridge

The engine's `iter_events` is a **blocking** generator; the FastAPI endpoint is async. Bridge without
stalling the event loop:

1. Run `iter_events` in a worker thread; each event is put on a `queue.Queue`, a sentinel marks the end.
2. An async generator does `await anyio.to_thread.run_sync(queue.get)` in a loop, converting each
   event to an SSE message (`event: <type>`, `data: <event.model_dump_json()>`), until the sentinel.
3. Wrap in `sse-starlette`'s `EventSourceResponse`.

As sections stream, the driver persists them (via the store/cache on the worker thread — this thread
owns the run, consistent with §2.3) and backfills `vendor_key` after `EntityResolved`.

---

## 5. Testing (zero credits)

- **`test_engine.py`** (extends/replaces `test_pipeline.py`):
  - **Parity:** `ReportEngine(mode="sequential")` and `mode="parallel")` produce identical `Report`
    from the same fakes (proves the refactor + parallelism preserve behavior).
  - **Event sequence:** `iter_events` yields `EntityResolved` first, one `SectionComplete` per
    dimension (7 total incl. backlog), `ReportComplete` last.
  - **Cache-hit streaming:** a pre-populated cache makes those dimensions stream back with
    `cached=True` and no worker computation.
  - **`SectionError`:** a fake whose one dimension raises yields a `SectionError` and the report still
    completes over the rest.
  - Existing Phase 1 pipeline behaviors (entity caching, backlog news reuse, dimension coverage)
    preserved.
- **`test_store.py`** — projects/vendors CRUD + `set_vendor_key` backfill, temp SQLite.
- **`test_sse_bridge.py`** — the sync→async queue bridge yields events in order, terminates on the
  sentinel, and propagates a worker error as a terminal event.
- **`test_api.py`** — FastAPI `TestClient` with **injected fake deps**: create project, add vendor,
  list, `GET /projects/{id}` (empty then populated report summary), and the SSE endpoint yields the
  expected event sequence. No live API calls.

All I/O seams (Tavily, Nebius, transcript fetch) use fakes injected via `Deps` / the `create_app`
factory, so the suite spends no credits.

---

## 6. Dependencies & tooling

- Add `sse-starlette` (SSE responses). `fastapi`, `uvicorn`, `httpx` already present (`httpx` also
  powers `starlette.testclient`).
- Run locally: `uv run uvicorn vendor_dd.surfaces.api.app:build_app --factory --reload` (or a small
  `vendor-dd-api` script entry that calls `build_app`).

---

## 7. Scope

**In scope (Phase 2):** the `ReportEngine` refactor (parallel + streaming + events), the `sources`
column, the `Store`, the REST + SSE endpoints, CORS, the test suite above.

**Deferred (later phases):**
- **Phase 3:** the React comparison-table frontend (consumes this API's `GET /projects/{id}` +
  `GET /vendors/{id}/report/stream`); chat over cached `sources`.
- **Phase 4:** eval harness + tracing (LangSmith / OTel spans across the engine; Tavily `session_id`).
- **Phase 5:** MCP server (`check_vendor`).
- **Not now:** auth, multi-user, migrations framework (single-file SQLite is fine for the take-home),
  Extract-based grounding, adaptive follow-up searches.

---

## 8. File structure (Phase 2)

```
backend/
  pyproject.toml                       # + sse-starlette; + vendor-dd-api script (optional)
  src/vendor_dd/
    engine/
      pipeline.py    # ReportEngine class (mode param), _compute_section, run_report shim
      events.py      # ReportEvent union (EntityResolved, SectionComplete, SectionError, ReportComplete, ReportError)
      cache.py       # + sources column, + all_sections()
    surfaces/
      cli.py         # renders iter_events / run_report; handles SectionError
      api/
        __init__.py
        app.py       # create_app(deps, db_path) factory: CORS, routers, SSE
        store.py     # Store: projects/vendors CRUD + vendor_key backfill
        routes.py    # REST + SSE endpoint handlers
        sse.py       # blocking-generator -> async EventSourceResponse bridge
        schemas.py   # API request/response models
  tests/
    test_engine.py  test_store.py  test_sse_bridge.py  test_api.py
```
