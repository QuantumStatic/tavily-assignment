# Vendor Due-Diligence — Phase 4: Logging + Review-Driven Fixes — Design

**Status:** complete draft (all three review workstreams folded in), pending user review.
**Date:** 2026-07-10
**Depends on:** Phases 1–3 (engine, API/SSE, frontend), all merged to `main`.

Phase 4 bundles three workstreams the user chose to fold together:
- **Part A — Structured logging system** (the feature).
- **Part B — Frontend fixes** (from the design + code reviews).
- **Part C — Backend/architecture fixes** (from the in-flight whole-project review — appended when it lands).

---

## PART A — Logging system

### A1. Five named loggers → five files

Python stdlib `logging`, five independent named loggers, each `propagate=False` with its own
`RotatingFileHandler` (10 MB × 5 backups):

| Logger name | File | Records | Hook point |
|---|---|---|---|
| `vendor_dd.general` | `general.log` | app/CLI startup+shutdown, report-run start/end, uncaught errors | `app.py`, `cli.py`, `pipeline.py` |
| `vendor_dd.llm` | `llm.log` | each LLM call: request (model, prompt, schema, params) + response (raw content, parsed, latency, usage) + errors | `llm.py` |
| `vendor_dd.tavily` | `tavily.log` | each search: inputs (query, params) + outputs (result count, results, latency) + errors | `tavily_client.py` |
| `vendor_dd.http` | `http.log` | every HTTP request + response (method, path, status, latency, body) | new FastAPI middleware |
| `vendor_dd.db` | `db.log` | every raw SQL statement + params + result (rowcount / lastrowid / row count fetched) | `cache.py` + `store.py` |

### A2. Format — JSON lines

One JSON object per line via a custom `JsonLineFormatter`. Every record carries:
`ts` (ISO-8601 UTC), `level`, `logger`, `correlation_id`, `event` (a short slug), plus event-specific
fields passed via `logger.info(event, extra={"payload": {...}})`. Parseable with `jq`; the payloads
(prompts, results, SQL) are structured anyway.

### A3. Correlation ID

A `contextvars.ContextVar[str | None]` (`correlation_id_var`) stamped on every record across all five
files, so one HTTP request or CLI run can be traced end-to-end (HTTP → SQL → Tavily → LLM → HTTP).
- The **HTTP middleware** generates a request id (uuid4 hex, or honours an inbound `X-Request-ID`
  header) and sets it for the duration of the request.
- The **CLI** sets one per invocation.
- The engine fans dimensions out to worker threads; `contextvars` don't auto-propagate into a
  `ThreadPoolExecutor`, so `pipeline.py` wraps each `pool.submit(...)` with `contextvars.copy_context()`
  (`pool.submit(ctx.run, fn, *args)`) so worker-thread LLM/Tavily/DB logs keep the id. When no id is
  set (e.g. a bare unit test), it logs `null`.

### A4. Safety (hard requirements)

- **Never log secrets.** We log *logical* inputs (prompt, query, SQL + bound params), never transport
  auth. A `redact()` pass in the formatter scrubs any value equal to a known secret env var
  (`NEBIUS_API_KEY`, `TAVILY_API_KEY`) and any obvious key-shaped token, replacing with `"***"`.
- **Logging never breaks a request.** Every instrumentation call site is wrapped so a logging
  exception is swallowed (the logging module already isolates handler errors; we additionally guard
  our own payload-building code).
- **SSE responses are never buffered.** The HTTP middleware logs the request + the response
  status/latency, but only logs a *body* for non-streaming responses under a size cap
  (e.g. 4 KB, truncated). It detects the streaming response (`EventSourceResponse` / no known
  content-length / `text/event-stream`) and skips body capture, so the report stream is untouched.
- **Bodies truncated.** Request/response and LLM/Tavily payload bodies are truncated to a configurable
  max length so a huge prompt or result set can't bloat a log line unboundedly.

### A5. Configuration

`configure_logging(log_dir: Path | str = "logs", level: str = "INFO") -> None` — idempotent; builds the
five handlers/formatters and attaches them. Called once at:
- `build_app()` (API) — before the app serves.
- `cli.main` (CLI) — at the top of a run.
`log_dir` resolves from `VENDOR_DD_LOG_DIR` env (default `logs/` relative to cwd). The `logs/`
directory is created if missing and is git-ignored. `VENDOR_DD_LOG_LEVEL` overrides level.

### A6. Wiring detail

- **llm.py** — `NebiusLLM.structured()`: log an `llm.request` record (model, prompt, schema name,
  reasoning_effort) before the call; on success an `llm.response` record (raw content, parsed dict,
  latency_ms, token usage if present); on exception an `llm.error` record. Never logs the api_key.
- **tavily_client.py** — `TavilySearchClient.search()`: `tavily.request` (kwargs incl. query/params,
  minus any auth) + `tavily.response` (result count + truncated results + latency) + `tavily.error`.
- **cache.py / store.py** — a private `_exec(sql, params)` helper on each class routes every
  `self._conn.execute(...)` through one place that emits a `db.query` record (sql text, params,
  rowcount/lastrowid) — so all SQL logs uniformly without sprinkling calls.
- **middleware.py** (new, `surfaces/api/`) — an ASGI/HTTP middleware: sets the correlation id, logs
  `http.request` (method, path, query, client, body-if-small) and `http.response` (status, latency_ms,
  body-if-small-and-not-streaming).
- **pipeline.py** — `general` logs at run start (`report.start`, vendor, mode) and end
  (`report.complete`/`report.error`), and the `copy_context()` submit wrapping (A3).
- **app.py / cli.py** — call `configure_logging()`; `general` logs `app.startup` / `cli.run`.

### A7. Testing (all zero-credit)

`backend/tests/test_logging.py`:
- `JsonLineFormatter` emits valid one-object-per-line JSON with the required keys.
- `redact()` scrubs known secret values and leaves normal text alone.
- Correlation id: set the contextvar → a record carries it; unset → `null`.
- `configure_logging(tmp_path)` creates the five files; a log call lands in the right file only
  (propagate isolation).
- LLM logging: drive `NebiusLLM.structured()` with a **fake** OpenAI client → assert `llm.request` +
  `llm.response` lines with expected fields and **no api_key** present.
- DB logging: a `SQLiteCache.put/get` (or `Store.create_project`) writes a `db.query` line with the
  SQL + params.
- HTTP middleware: a `TestClient` request writes `http.request`/`http.response` with status + latency;
  a request to the SSE stream endpoint does **not** buffer/log the stream body.
- Thread propagation: a `ReportEngine(mode="parallel")` run (fakes) → worker-thread LLM logs carry the
  driver's correlation id.

### A8. File structure (Part A)

```
backend/src/vendor_dd/
  logs/                       # NEW package (name doesn't shadow stdlib `logging`)
    __init__.py               # public API: configure_logging, get_logger, correlation_id_var, set_correlation_id
    setup.py                  # handlers/loggers/config + contextvar
    formatter.py              # JsonLineFormatter + redact()
  engine/llm.py               # + llm logging
  engine/tavily_client.py     # + tavily logging
  engine/cache.py             # + _exec() db logging
  engine/pipeline.py          # + general run logs + copy_context submit
  surfaces/api/store.py       # + _exec() db logging
  surfaces/api/middleware.py  # NEW: http request/response logging middleware
  surfaces/api/app.py         # + configure_logging(); register middleware
  surfaces/cli.py             # + configure_logging(); per-run correlation id
backend/tests/test_logging.py # NEW
logs/                         # git-ignored
```

---

## PART B — Frontend fixes (from the design + code reviews)

Grouped by priority. **B1–B7 are the must-fix set** (correctness bugs + the highest-value
visual/a11y). B8+ are polish, done if cheap.

### Correctness bugs (both reviews / code review)

- **B1 — Verdict pill hardcoded green.** `ReportPanel.tsx:25` renders `className="pill good"`
  unconditionally; a low verdict shows green in the panel while the row shows red. Fix:
  `className={\`pill ${bandForScore(verdict.score)}\`}` (reuse `band.ts`). *Both reviews, Critical.*
  Test: `panel.test.tsx` with a low-score verdict asserts `pill bad`.
- **B2 — Stream drop leaves the row spinning "pending" forever.** `App.tsx` `onError` only sets the
  (dismissable) banner; the row's cells never leave `pending`. Fix: pass `vendorId` into the stream's
  `onError` and dispatch a row-level failure (a `report_error`-style action → `status:'error'`,
  verdict `failed`) so the row shows a failed/retry state. Test: drive `FakeEventSource.fail()` through
  `App` and assert the row renders failed, not pending.
- **B3 — Delete failure leaves a stuck row.** `App.tsx removeVendor` closes+deletes the stream from the
  map *before* awaiting `api.deleteVendor`; if DELETE fails the row stays but generation is dead. Fix:
  tear down the stream only *after* a successful delete (move the `streams.get(id)?.()` +
  `.delete(id)` into the try, after the `await`). Test: mock DELETE 500 → row still present AND its
  stream state coherent.
- **B4 — Malformed SSE frame white-screens the whole app.** The `{type, ...data}` cast in `stream.ts`
  lets a wrong-shape frame reach `reduceEvent` during render → `TypeError` → root unmounts (no error
  boundary). Fix: add an `ErrorBoundary` in `main.tsx` (renders a fallback instead of a blank page).
  (Optional stretch: validate frame shape per `type` before dispatch.) Test: an error-boundary unit
  test with a throwing child.

### Theme / visual

- **B5 — Dark-mode FOUC on every load.** `data-theme` is set in a `useEffect` post-mount and there's no
  `@media (prefers-color-scheme)` fallback → dark users get a light first paint. Fix: a tiny blocking
  inline script in `index.html <head>` that reads `localStorage`/`matchMedia` and sets
  `document.documentElement.dataset.theme` before the bundle loads (keep `useTheme` as the source of
  truth thereafter). No FOUC on reload.
- **B6 — Theme toggle collides with the report panel's ✕.** Fixed `top:10px;right:12px;z-index:1000`
  sits on the panel's close button when the panel is open. Fix: move the toggle into the **toolbar**
  (top-right of the main content area, left of/above the AddVendorForm) so it never overlaps the panel;
  keep it visible even with no active project by giving the header a persistent right-aligned slot.
  *(Chosen over the offset-when-open hack — simpler and always conflict-free.)* Test: it renders and
  toggles regardless of panel state.
- **B7 — Contrast failures on gray tokens (WCAG AA).** Fix in `styles.css`:
  - light `--text-muted` `#888888` (~3.5:1) → `~#6b7280` (~4.7:1)
  - light `--text-faint` `#bbbbbb` (~1.9:1) → `~#8a8a8a` (~4.6:1)
  - dark `--text-faint` `#667079` (~3.5:1) → lift toward `~#8b95a1` (≥4.5:1)
  Clears the citation-metadata, table-header, and muted-reasoning contrast failures. (Pills already
  pass in both themes — leave them.)

### A11y / polish (do if cheap; skip deep refactors)

- **B8 — Error banner:** add `role="alert"` + a visible ✕ dismiss (currently click-anywhere, no icon,
  no live region).
- **B9 — Pulsing dot:** add a `@media (prefers-reduced-motion: reduce)` static fallback + an sr-only
  "pending" label so non-visual users aren't left with an empty cell.
- **B10 — Verdict column distinctness:** a subtle `.verdict-cell` tint/left-border so the headline
  number is anchored vs. the seven dimension cells.
- **B11 — Table robustness:** wrap the table in an `overflow-x:auto` container; `text-overflow:ellipsis`
  on `.vendor-name`; `position:sticky; top:0` on `th`.
- **B12 — Theme transition:** either extend the `.2s` transition to surface/border-bearing elements or
  drop it for a clean instant swap (currently only `body` animates → janky).
- **B13 — Copy/CTA:** differentiate the duplicated "Create a project" empty states
  (`App.tsx` vs `Sidebar.tsx`); give the add-vendor button more emphasis.

**Explicitly deferred (out of Phase 4):** the `role="button"` on `<tr>` semantics rework (B-review #7 /
code #10) — a real a11y improvement but a structural change to the table interaction model; note it for
a later pass. Shared `TextSubmitForm` extraction and `useVendorStreams` hook extraction — maintainability
nice-to-haves, not folded in now.

---

## PART C — Backend / architecture fixes (from the whole-project review)

The review confirmed the architecture is sound — surface-agnostic engine (CLI + API consume the same
`iter_events` generator), the worker-threads-touch-no-DB concurrency invariant holds end-to-end,
disciplined cache/store lifecycle, fully-parameterized SQL, secrets never logged, `pyproject.toml`
clean. **No Critical issues.** Folded fixes, prioritized:

### Must-fix

- **C1 — Guard LLM response extraction (SPOF hardening).** `llm.py structured()` does
  `json.loads(resp.choices[0].message.content)` with three unguarded failure modes (empty `choices` →
  `IndexError`; `content is None` → `TypeError`; reasoning-leak → `JSONDecodeError`). On the six
  dimension calls these degrade to `SectionError`, but on the **entity path** (the pipeline
  single-point-of-failure) any becomes a fatal `ReportError` with a cryptic message like
  `"list index out of range"`. Fix: extract defensively and raise a typed `LLMError` with a clear
  message when the response is unusable; add **one retry** for entity resolution specifically. Pairs
  with the `llm.log` instrumentation (A6). Tests: empty-choices / None-content / bad-JSON each raise a
  clear typed error (fake client).
- **C2 — Read-model completeness signal (frontend contract).** `_summarize`/`get_report` set
  `generated=true` and run `assemble_verdict` over *any* cached subset — an interrupted run shows a
  confident verdict computed from 2 of 7 sections as if finished. Fix: expose completeness in the read
  model (`sections_present` + `sections_expected`, or a `complete: bool`) so the frontend can tell
  partial from complete; decide whether to redefine `generated` or keep it as "≥1 section" in the plan.
  Consumed by Part B. Tests: cache a subset → assert the completeness fields.

### Cheap wins (fold in)

- **C3 — SQLite WAL + busy_timeout.** `cache.py`/`store.py` open with `check_same_thread=False` but no
  `journal_mode=WAL` / explicit `busy_timeout`. Add both PRAGMAs in each constructor so readers don't
  block writers and contention is explicit. (Verified not a current "database is locked" bug — Python's
  default 5 s timeout absorbs it — but correct hygiene under concurrent streams.)
- **C4 — Log internals, send generic to clients.** `SectionError`/`ReportError` currently stream
  `str(exc)` (internal URLs/paths possible) to the browser. With Part A logging: log the full exception
  server-side, put a generic message in the client-facing event. Direct synergy with logging.
- **C5 — "N results filtered" observability.** The anti-contamination filter (`filtering.py`) can
  silently drop *every* result on a canonical-name mismatch (e.g. "Alphabet" vs "Google") → empty
  section, artificially low score, no signal that filtering (not reality) caused it. With logging: log
  kept/filtered counts per dimension so this is visible. (Fuzzier matching is optional/deferred.)
- **C6 — Recurse `_coerce_null_strings`.** It coerces literal `"null"` strings only at the top level;
  a nested `Citation.as_of="null"` slips through — narrower than its docstring claims. Make it recurse.
  Cheap (already editing `llm.py`). Test: nested `"null"`.

### Deferred (noted, NOT in Phase 4)

- **In-flight de-duplication** of concurrent identical streams (two clients streaming the same vendor
  both spend full Tavily/LLM credits) — a real cost concern, but needs a vendor-key lock / in-flight
  registry; that's a feature, not a fix. Correctness is fine (`REPLACE` is idempotent).
- **Entity TTL (30 d) vs sections (TTL-ignored) mismatch** → `entity: null` with `generated: true`.
  No backend change; Part B's frontend already tolerates a null entity (ReportPanel falls back to
  `row.name`) — but see the Part B partial-report note below.
- Dead `DIMENSION_CONFIGS[SNAPSHOT/BACKLOG]` query fields (harmless, misleading) and the shared
  `requests.Session` across worker threads (low-probability) — noted for a later cleanup.

### Backend test additions (fold in)

- `llm.py` malformed-response paths (with C1) — highest-value gap.
- `_coerce_null_strings` nested (with C6).
- read-model partial-data (with C2).
- (optional) `backlog.py` `fetch_transcript_text` HTTP-error path + `build_quote_url` /
  `parse_transcript_path` branch coverage.

### Cross-cutting note (Part B ⇄ C2/entity-null)

Part B must make the frontend's partial/incomplete-report path graceful: `selectVendor` currently
early-returns when `getReport` yields a generated vendor missing `entity`/verdict (→ an empty panel).
With C2's completeness signal, the panel should show a "report incomplete / still generating" state
rather than a blank body, and tolerate `entity: null`.

---

## Ordering & execution

Implement **Part A (logging)** and **Part B (frontend fixes)** as independent task groups (they don't
touch the same files). Backend fixes (Part C) fold in once the review lands. Every change is TDD /
test-backed and zero-credit (fakes + tmp dirs). Frontend fixes land in the running checkout so Vite
HMR shows them live.
