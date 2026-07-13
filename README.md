# Vendor Due-Diligence Agent

Give it a vendor name, get back a structured, **cited** risk profile a procurement team
could actually file — company snapshot, financial signals, legal & regulatory exposure,
safety record, certifications, delivery/backlog health, recent news, and an overall
verdict. Every finding carries a source URL, a source *type* (independent vs. the vendor's
own material), and an "as of" date. Reports stream in dimension-by-dimension, and every
score is remembered over time so you can watch a vendor's risk trend.

Built on [Tavily](https://tavily.com) for retrieval and OpenAI's Responses API for
structured synthesis.

---

## Why this

The starter agent does one thing: a single Tavily search, summarized by an LLM into prose.
That's a demo, not a tool someone can make a decision on. For a real procurement or
vendor-risk workflow, the gaps are: *where did this claim come from, how fresh is it, can I
trust the source, and how does this vendor compare to the others I'm evaluating?*

This turns that one-shot search into a **decision-grade workflow**:

- **Every claim is cited and typed.** A finding isn't "the vendor has legal issues" — it's a
  claim, a URL, whether the source is independent or self-reported, and a date. A reviewer
  can audit it. Scores are derived from findings, not vibes.
- **Retrieval is engineered per risk dimension**, not one keyword-stuffed query — see
  [How Tavily is used](#how-tavily-is-used).
- **It's a portfolio, not a single lookup.** Vendors live in projects; a comparison table,
  a portfolio dashboard (shortlist / red flags / most-trusted), and a score-over-time chart
  turn N reports into a decision.
- **It's observable.** Structured JSON logs across five channels, correlation IDs that follow
  a run from HTTP request into the background worker, secret redaction — so you can debug a
  bad report instead of guessing.

The through-line: **auditability and comparison** are what make retrieval useful to a
customer, so that's where the engineering went.

## Demo

> _Screenshot / GIF placeholder — see [Run it](#run-it) to bring it up locally._
> The Overview shows the portfolio dashboard + score-trend chart; opening a project shows
> the live comparison table filling in over SSE; clicking a vendor opens its cited report.

## How it works

```
vendor name
    │
    ▼
┌─────────────────┐   entity resolution: resolve the messy input ("Voith Hydro") to a
│  Snapshot       │   canonical company + domain, so every downstream query and every
│  (entity card)  │   cache key is stable across renames and spelling variants.
└────────┬────────┘
         │  domain (vendor_key)
         ▼
┌──────────────────────────────────────────────┐   6 scored dimensions, retrieved in
│  legal · safety · financial · certifications  │   parallel. Each = several focused
│  backlog · news        (parallel retrieval)   │   Tavily queries → pooled + deduped.
└────────┬─────────────────────────────────────┘
         │  raw results per dimension
         ▼
┌─────────────────┐   OpenAI Responses API (structured output): each dimension → a Section
│  Synthesis      │   of {claim, citation(url, source_type, as_of)} findings + a 0–10 score
│  (per section)  │   + reasoning. Nothing free-text-parsed; the schema is enforced.
└────────┬────────┘
         │  scored, cited sections
         ▼
┌─────────────────┐   overall verdict + reasoning. Each section is cached (per-domain,
│  Verdict        │   TTL per dimension) and every score is appended to history for trends.
└─────────────────┘
         │
         ▼  streamed to the UI over SSE as each dimension lands
```

Each dimension is cached independently keyed by resolved **domain**, so a rename never
loses research and two projects evaluating the same vendor share one set of lookups.

## How Tavily is used

Retrieval quality is the product, so this is where most of the design went. Per dimension
(`engine/config.py`):

- **Several focused queries instead of one.** `legal` runs `"{name} lawsuit"`,
  `"{name} litigation"`, `"{name} regulatory fine"`, `"{name} investigation"` separately, then
  pools and dedupes. Each single-concept query gets clean relevance instead of one
  keyword-stuffed query fighting itself.
- **Per-dimension parameter tuning** — `topic` (`general`/`news`/`finance`), `search_depth`,
  `max_results`, an optional recency window, and Tavily's `country` param are set per
  dimension because "recent legal filings" and "latest audited financials" want different
  retrieval.
- **Source independence is enforced.** Independent dimensions (legal, safety) pass
  `exclude_domains=[vendor domain]` so the vendor can't be the source of its own clean bill
  of health. Financials deliberately *don't* — audited figures are self-reported by nature —
  and each finding is tagged `independent` vs `self_reported` so the distinction is visible,
  not hidden.
- **Search on the press name, not the legal name.** Entity resolution picks the name articles
  actually use ("GE Vernova", not "GE Vernova LLC"), which materially changes recall.
- **A recency-window lesson worth calling out:** Tavily's `start_date` silently drops any
  result it can't assign a date to — for some vendors that's *every* result, yielding zero
  news. So dimensions where freshness is a bonus (news, financials) use no hard window and
  instead carry per-finding `as_of` dates. This is the kind of thing you only learn by
  actually running retrieval against obscure private vendors, not just big public ones.

Results are cached per (domain, dimension) with a per-dimension TTL, so re-opening a report
is instant and doesn't re-spend Tavily/LLM credits.

## Observability

The assignment calls out tracing/observability as a bonus; here it's first-class and
industry-standard in shape (structured logs + correlation), without pulling in a vendor SDK:

- **Five JSON-line channels** — `general`, `llm`, `tavily`, `http`, `db` — one structured
  event per line (`ts, level, logger, correlation_id, event, payload`), rotating files.
- **Correlation IDs** are minted per CLI run and per HTTP request and propagated via
  `contextvars` — including *into the background report thread* (`copy_context`) — so a whole
  report generation is greppable by one ID across search, LLM, and DB calls.
- **Secret redaction** in the formatter: `TAVILY_API_KEY` / `OPENAI_API_KEY` values are
  scrubbed from any logged payload.

LangSmith / OpenTelemetry span export is the natural next step (see [What's next](#whats-next)),
but the correlation-ID + structured-event backbone is already what a trace exporter would sit on.

## Architecture

```
backend/
  src/vendor_dd/
    db.py            shared SQLite connection setup (WAL, logged exec) for the stores below
    engine/          surface-agnostic core — no web framework here
      config.py      per-dimension retrieval tuning
      tavily_client  Tavily search wrapper + params
      retrieval.py   multi-query pooling + dedup
      llm.py         OpenAI Responses API, structured outputs
      synthesis.py   results → scored, cited Section
      pipeline.py    orchestration; emits events (snapshot → sections → verdict)
      cache.py       per-(domain, dimension) TTL cache
      history.py     append-only score history (powers the trend chart)
      schemas.py     Dimension / Section / Citation / EntityCard / Report
    surfaces/        thin adapters over the engine
      cli.py         one-shot terminal report
      api/           FastAPI: projects/vendors CRUD, SSE streaming, dashboard + trend
  tests/             184 tests
frontend/            React + Vite + TS — projects, live comparison table, report panel,
  src/               portfolio dashboard, score-trend chart (114 tests)
evals/ground_truth/  fixtures for the planned citation-support eval (see What's next)
```

Notable engine/surface decisions:

- **Engine knows nothing about HTTP.** The CLI and the API are both thin adapters over the
  same `iter_events` pipeline. Adding an MCP surface later is another adapter, not a rewrite.
- **SSE with a single-flight run registry.** Two tabs streaming the same vendor subscribe to
  one generation (history replayed, then live events) instead of double-spending on Tavily/LLM.
  Generation runs in a background thread and drains to the cache even if the client disconnects.
- **SQLite for cache, project store, and score history**, sharing one file behind a small
  `SqliteConn` base. Writes to the project store are serialized with a lock because one
  connection is shared across the threadpool and the background worker.

## Run it

Prereqs: Python ≥3.11 with [`uv`](https://docs.astral.sh/uv/), Node ≥18. Keys for Tavily and
OpenAI.

```bash
cp .env.example .env      # fill in TAVILY_API_KEY and OPENAI_API_KEY
```

**CLI** (fastest way to see a report):

```bash
cd backend
uv run vendor-dd "GE Vernova"
```

**Full app** (API + web UI):

```bash
# terminal 1 — API on :8000
cd backend && uv run vendor-dd-api

# terminal 2 — UI on :5173
cd frontend && cp .env.example .env && npm install && npm run dev
```

Open http://localhost:5173, create a project, add vendors, and watch reports stream in.

## Testing

```bash
cd backend && uv run pytest        # 184 tests
cd frontend && npm test            # 114 tests (vitest)
```

The engine is built from **pure functions** (retrieval pooling, synthesis, verdict assembly,
dashboard aggregation, monthly bucketing) that take data and return data, so the bulk of the
suite runs with no network and no mocks-of-mocks. LLM and Tavily are behind small `Protocol`
interfaces with fakes. Development was test-first throughout.

## Design decisions & tradeoffs

- **Scores from findings, not free text.** The LLM emits a schema (findings + citations +
  score), enforced by the Responses API `parse` — no regex-scraping model prose. Costs some
  prompt engineering; buys auditability and stable parsing.
- **Cache/history keyed by resolved domain, not the typed name.** "Voith" and "Voith Hydro"
  resolve to one domain and share research; a rename keeps its history. Entity resolution is
  the price.
- **No vector DB.** Retrieval is Tavily-native and per-dimension; findings are small and
  structured. A vector store would be complexity without a job here — YAGNI.
- **SQLite over Postgres.** Single-file, zero-ops, fits a take-home and a single-node
  deployment. The `SqliteConn` seam makes swapping it out mechanical if scale demanded it.

## What's next

Honest about what's stubbed or deferred:

- **Eval loop** — `evals/ground_truth/` holds fixtures; the runner (citation-support +
  contamination checks against known vendors) is designed but not built. This is the highest-
  value next increment for trust.
- **Trace export** — wire the existing correlation-ID/structured-event backbone to
  OpenTelemetry or LangSmith spans (per report run / per dimension).
- **MCP surface** — expose `check_vendor(name)` as an MCP tool over the same engine.
- **Streaming lock scope** — the per-domain single-flight lock is currently held across SSE
  yields; narrowing it to the cache-write window would stop a slow consumer serializing
  same-domain work.

## How this was built

This was built with heavy use of a coding agent (Claude Code), directed test-first: spec →
plan → red/green/refactor per task, with the design docs and plans under `docs/superpowers/`.
The session logs are part of the submission — the intent is to show *direction* of AI tools,
not unverified output.
