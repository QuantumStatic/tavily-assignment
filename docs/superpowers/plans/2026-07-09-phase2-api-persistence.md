# Vendor Due-Diligence — Phase 2: API + Persistence + Streaming Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a FastAPI service in front of the Phase 1 engine — project/vendor persistence, a REST read model for the comparison table, and an SSE endpoint that streams a due-diligence report as each dimension completes — backed by a refactored `ReportEngine` that fans dimensions out in parallel and emits typed events.

**Architecture:** The engine becomes one `ReportEngine` class with a `mode` parameter (`"parallel"` via `ThreadPoolExecutor`, `"sequential"` via a single worker). It exposes `iter_events()` (a streaming generator of typed `ReportEvent`s) and `run_report()` (drains the generator to a `Report`). Worker threads do only pure retrieve+synthesize; the driver thread owns all SQLite I/O. A thin FastAPI layer (`surfaces/api/`) persists projects/vendors, assembles a read model from the cache, and streams engine events over `sse-starlette`'s `EventSourceResponse`. Every I/O seam is injected, so the whole test suite runs on zero Tavily/Nebius credits.

**Tech Stack:** Python 3.11+, Pydantic v2, `concurrent.futures.ThreadPoolExecutor`, FastAPI, `sse-starlette`, SQLite (stdlib), pytest + `fastapi.testclient`.

Spec: `docs/superpowers/specs/2026-07-09-phase2-api-persistence-design.md`

---

## File structure (Phase 2)

```
backend/
  pyproject.toml                       # + sse-starlette; + vendor-dd-api script
  src/vendor_dd/
    engine/
      events.py     # NEW: ReportEvent union (EntityResolved/SectionComplete/SectionError/ReportComplete/ReportError)
      cache.py      # MODIFY: + sources column, + all_sections()
      pipeline.py   # MODIFY: ReportEngine class (mode), iter_events, run_report shim, _compute_section
    surfaces/
      cli.py        # MODIFY: stream via iter_events; print SectionError
      api/
        __init__.py # NEW (empty)
        schemas.py  # NEW: API request/response models
        store.py    # NEW: Store (projects/vendors CRUD + vendor_key backfill)
        sse.py      # NEW: to_sse_frame(event) -> dict
        app.py      # NEW: create_app(deps) factory + build_app() + run()
        routes.py   # NEW: REST + SSE endpoint handlers
  tests/
    test_engine.py  # NEW: engine event/parity/error tests
    test_cache.py   # MODIFY: + sources + all_sections tests
    test_store.py   # NEW
    test_sse.py     # NEW
    test_api.py     # NEW
```

**Runnable milestones:**
- After Task 3: the engine streams events + still passes all Phase 1 tests.
- After Task 8: REST API (projects/vendors CRUD + read model) works via `TestClient` with fakes.
- After Task 9: the SSE endpoint streams a full report; `uv run uvicorn ... --factory` serves it.

---

## Task 1: Engine event types

**Files:**
- Create: `backend/src/vendor_dd/engine/events.py`
- Test: `backend/tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_engine.py
from vendor_dd.engine.schemas import Dimension, EntityCard, Section, Report
from vendor_dd.engine.events import (
    EntityResolved, SectionComplete, SectionError, ReportComplete, ReportError,
)


def _entity():
    return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                      industry="steel", is_public=False)


def _section():
    return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


def test_event_types_carry_discriminator_and_payload():
    assert EntityResolved(entity=_entity()).type == "entity_resolved"
    sc = SectionComplete(section=_section(), cached=True)
    assert sc.type == "section_complete" and sc.cached is True
    se = SectionError(dimension=Dimension.FINANCIAL, message="boom")
    assert se.type == "section_error" and se.dimension is Dimension.FINANCIAL
    rc = ReportComplete(report=Report(vendor_input="x", entity=_entity(), sections=[],
                                      verdict_score=5, verdict_reasoning="r"))
    assert rc.type == "report_complete"
    assert ReportError(message="fatal").type == "report_error"


def test_section_complete_defaults_cached_false():
    assert SectionComplete(section=_section()).cached is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.events`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/engine/events.py
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel

from vendor_dd.engine.schemas import Dimension, EntityCard, Report, Section


class EntityResolved(BaseModel):
    type: Literal["entity_resolved"] = "entity_resolved"
    entity: EntityCard


class SectionComplete(BaseModel):
    type: Literal["section_complete"] = "section_complete"
    section: Section
    cached: bool = False


class SectionError(BaseModel):
    type: Literal["section_error"] = "section_error"
    dimension: Dimension
    message: str


class ReportComplete(BaseModel):
    type: Literal["report_complete"] = "report_complete"
    report: Report


class ReportError(BaseModel):
    type: Literal["report_error"] = "report_error"
    message: str


ReportEvent = Union[
    EntityResolved, SectionComplete, SectionError, ReportComplete, ReportError,
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_engine.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/events.py backend/tests/test_engine.py
git commit -m "feat(engine): typed report stream events"
```

---

## Task 2: Cache — `sources` column + `all_sections()`

**Files:**
- Modify: `backend/src/vendor_dd/engine/cache.py`
- Test: `backend/tests/test_cache.py` (append)

- [ ] **Step 1: Write the failing test (append to test_cache.py)**

```python
# append to backend/tests/test_cache.py
def test_put_persists_and_returns_sources_via_all_sections(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.LEGAL, {"score": 5},
              sources=[{"url": "https://a.com", "score": 0.7}])
    got = cache.all_sections("acme.com")
    assert Dimension.LEGAL in got
    content, fetched = got[Dimension.LEGAL]
    assert content == {"score": 5}
    assert fetched == _now()


def test_all_sections_ignores_ttl_and_returns_everything(tmp_path):
    t = {"now": _now()}
    cache = SQLiteCache(tmp_path / "c.db", clock=lambda: t["now"])
    cache.put("acme.com", Dimension.NEWS_POSITIVE, {"score": 9})  # 1-day TTL
    cache.put("acme.com", Dimension.LEGAL, {"score": 4})
    t["now"] = _now() + timedelta(days=30)  # everything is now stale for get()
    assert cache.get("acme.com", Dimension.NEWS_POSITIVE) is None  # get() honors TTL
    got = cache.all_sections("acme.com")                            # all_sections does not
    assert set(got) == {Dimension.NEWS_POSITIVE, Dimension.LEGAL}


def test_put_without_sources_still_works(tmp_path):
    cache = SQLiteCache(tmp_path / "c.db", clock=_now)
    cache.put("acme.com", Dimension.SNAPSHOT, {"name": "Acme"})   # no sources arg
    assert cache.get("acme.com", Dimension.SNAPSHOT) == {"name": "Acme"}
```

Note: `datetime`, `timedelta`, `timezone`, `Dimension`, `SQLiteCache`, and `_now` are already imported/defined at the top of the existing `test_cache.py` (from Phase 1). Do not duplicate them.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_cache.py -v`
Expected: FAIL — `TypeError: put() got an unexpected keyword argument 'sources'` (and `AttributeError: 'SQLiteCache' object has no attribute 'all_sections'`)

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `backend/src/vendor_dd/engine/cache.py` with:

```python
# backend/src/vendor_dd/engine/cache.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vendor_dd.engine.schemas import Dimension
from vendor_dd.engine.config import TTL


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteCache:
    """Per-(vendor_key, section_type) cache. TTL is applied on read from config.TTL."""

    def __init__(self, path: str | Path, clock: Callable[[], datetime] = _utcnow):
        self._clock = clock
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS report_cache (
                 vendor_key TEXT NOT NULL,
                 section_type TEXT NOT NULL,
                 content TEXT NOT NULL,
                 sources TEXT,
                 fetched_at TEXT NOT NULL,
                 PRIMARY KEY (vendor_key, section_type)
               )"""
        )
        self._conn.commit()

    def put(self, vendor_key: str, section: Dimension, content: dict[str, Any],
            sources: list[dict[str, Any]] | None = None) -> None:
        self._conn.execute(
            """REPLACE INTO report_cache (vendor_key, section_type, content, sources, fetched_at)
               VALUES (?,?,?,?,?)""",
            (vendor_key, section.value, json.dumps(content),
             json.dumps(sources) if sources is not None else None,
             self._clock().isoformat()),
        )
        self._conn.commit()

    def _row(self, vendor_key: str, section: Dimension) -> tuple[str, datetime] | None:
        cur = self._conn.execute(
            "SELECT content, fetched_at FROM report_cache WHERE vendor_key=? AND section_type=?",
            (vendor_key, section.value),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return row[0], datetime.fromisoformat(row[1])

    def get(self, vendor_key: str, section: Dimension) -> dict[str, Any] | None:
        row = self._row(vendor_key, section)
        if row is None:
            return None
        content, fetched_at = row
        if self._clock() - fetched_at >= TTL[section]:
            return None  # stale
        return json.loads(content)

    def fetched_at(self, vendor_key: str, section: Dimension) -> datetime | None:
        row = self._row(vendor_key, section)
        return row[1] if row else None

    def all_sections(self, vendor_key: str) -> dict[Dimension, tuple[dict[str, Any], datetime]]:
        """Every stored section for a vendor with its fetched_at, ignoring TTL.
        Freshness is a UI concern; the comparison table shows whatever's cached."""
        cur = self._conn.execute(
            "SELECT section_type, content, fetched_at FROM report_cache WHERE vendor_key=?",
            (vendor_key,),
        )
        out: dict[Dimension, tuple[dict[str, Any], datetime]] = {}
        for section_type, content, fetched_at in cur.fetchall():
            try:
                dim = Dimension(section_type)
            except ValueError:
                continue
            out[dim] = (json.loads(content), datetime.fromisoformat(fetched_at))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_cache.py -v`
Expected: PASS (all Phase 1 cache tests + 3 new)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/cache.py backend/tests/test_cache.py
git commit -m "feat(engine): cache sources column + all_sections() read"
```

---

## Task 3: `ReportEngine` — parallel/sequential, streaming events

**Files:**
- Modify: `backend/src/vendor_dd/engine/pipeline.py` (full rewrite; keeps `Deps` + `run_report` surface)
- Test: `backend/tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing test (append to test_engine.py)**

```python
# append to backend/tests/test_engine.py
from datetime import date
from vendor_dd.engine.pipeline import ReportEngine, Deps, run_report


class StatelessSearch:
    def search(self, **kwargs):
        return {"results": [{"title": "Cives Steel news", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


class StatelessLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


class OneDimFailsLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        if "financial" in prompt:
            raise RuntimeError("boom")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def _deps(tmp_path, llm=None):
    return Deps(search=StatelessSearch(), llm=llm or StatelessLLM(),
                cache_path=tmp_path / "c.db", today=date(2026, 7, 8),
                fetch_transcript=lambda url: (None, None))


def test_iter_events_sequence(tmp_path):
    engine = ReportEngine(_deps(tmp_path), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    assert events[0].type == "entity_resolved"
    assert events[-1].type == "report_complete"
    completed = [e for e in events if e.type == "section_complete"]
    # 6 Tavily dims + backlog = 7 sections
    dims = {e.section.dimension for e in completed}
    assert dims == {d for d in Dimension if d is not Dimension.SNAPSHOT}


def test_cache_hit_streams_cached_true(tmp_path):
    deps = _deps(tmp_path)
    engine = ReportEngine(deps, mode="sequential")
    list(engine.iter_events("Cives Steel"))            # populate cache
    events = list(engine.iter_events("Cives Steel"))   # second run: all cached
    completed = [e for e in events if e.type == "section_complete"]
    assert completed and all(e.cached for e in completed)


def test_section_error_does_not_abort_report(tmp_path):
    engine = ReportEngine(_deps(tmp_path, llm=OneDimFailsLLM()), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    errors = [e for e in events if e.type == "section_error"]
    assert [e.dimension for e in errors] == [Dimension.FINANCIAL]
    assert events[-1].type == "report_complete"          # report still completes
    dims = {e.section.dimension for e in events if e.type == "section_complete"}
    assert Dimension.FINANCIAL not in dims               # failed dim absent


def test_sequential_and_parallel_produce_same_report(tmp_path):
    seq = ReportEngine(_deps(tmp_path / "seq"), mode="sequential").run_report("Cives Steel")
    par = ReportEngine(_deps(tmp_path / "par"), mode="parallel").run_report("Cives Steel")
    assert seq.verdict_score == par.verdict_score
    assert {(s.dimension, s.score) for s in seq.sections} == \
           {(s.dimension, s.score) for s in par.sections}


def test_run_report_shim_still_returns_report(tmp_path):
    report = run_report("Cives Steel", _deps(tmp_path))
    assert isinstance(report, Report)
    assert report.entity.domain == "cives.com"
```

Note: `tmp_path / "seq"` and `tmp_path / "par"` are used only as distinct cache-file *paths* (SQLite creates the file); they need not exist as directories.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReportEngine' from 'vendor_dd.engine.pipeline'`

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `backend/src/vendor_dd/engine/pipeline.py` with:

```python
# backend/src/vendor_dd/engine/pipeline.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterator, Literal

from vendor_dd.engine.backlog import build_quote_url
from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.entity import resolve_entity
from vendor_dd.engine.events import (
    EntityResolved, ReportComplete, ReportEvent, SectionComplete, SectionError,
)
from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.retrieval import retrieve_dimension
from vendor_dd.engine.schemas import Dimension, EntityCard, Report, Section
from vendor_dd.engine.synthesis import assemble_verdict, synthesize_section
from vendor_dd.engine.tavily_client import SearchClient

# dimensions retrieved via Tavily (snapshot is entity resolution; backlog is special-cased)
_TAVILY_DIMS = [d for d in Dimension if d not in (Dimension.SNAPSHOT, Dimension.BACKLOG)]


@dataclass
class Deps:
    search: SearchClient
    llm: LLMClient
    cache_path: Path
    today: date
    fetch_transcript: Callable[[str], tuple[str | None, str | None]]


@dataclass
class DimensionOutcome:
    section: Section
    raw_results: list[dict]


def _normalize_name(name: str) -> str:
    return name.strip().lower()


class ReportEngine:
    """Runs a due-diligence report. `mode` selects concurrency; the algorithm is identical.

    - mode="parallel"   -> ThreadPoolExecutor(max_workers) fan-out (streams out-of-order)
    - mode="sequential" -> single worker (deterministic completion order)

    Threads (not async) because the work is I/O-bound. Worker threads do ONLY pure
    retrieve+synthesize; the driver thread owns every SQLite read/write.
    """

    def __init__(self, deps: Deps, *, mode: Literal["parallel", "sequential"] = "parallel",
                 max_workers: int = 6):
        self._deps = deps
        self._max_workers = 1 if mode == "sequential" else max_workers

    def run_report(self, vendor: str) -> Report:
        report: Report | None = None
        for ev in self.iter_events(vendor):
            if isinstance(ev, ReportComplete):
                report = ev.report
        assert report is not None, "iter_events must end with ReportComplete"
        return report

    def iter_events(self, vendor: str) -> Iterator[ReportEvent]:
        deps = self._deps
        cache = SQLiteCache(deps.cache_path)
        entity = self._resolve_entity_cached(vendor, cache)
        yield EntityResolved(entity=entity)

        vendor_key = (entity.domain or entity.name).strip().lower()
        sections: list[Section] = []
        news_positive_results: list[dict] | None = None

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            pending: dict = {}
            for dim in _TAVILY_DIMS:
                cached = cache.get(vendor_key, dim)
                if cached is not None:
                    section = Section.model_validate(cached)
                    sections.append(section)
                    yield SectionComplete(section=section, cached=True)
                    continue
                pending[pool.submit(self._compute_section, dim, entity)] = dim

            for fut in as_completed(pending):
                dim = pending[fut]
                try:
                    outcome = fut.result()
                except Exception as exc:  # one dimension failed; the report goes on without it
                    yield SectionError(dimension=dim, message=str(exc))
                    continue
                cache.put(vendor_key, dim, outcome.section.model_dump(mode="json"),
                          sources=outcome.raw_results)
                if dim is Dimension.NEWS_POSITIVE:
                    news_positive_results = outcome.raw_results
                sections.append(outcome.section)
                yield SectionComplete(section=outcome.section, cached=False)

        backlog = self._backlog_section(entity, cache, vendor_key, news_positive_results)
        sections.append(backlog)
        yield SectionComplete(section=backlog, cached=False)

        score, reasoning = assemble_verdict(sections)
        report = Report(vendor_input=vendor, entity=entity, sections=sections,
                        verdict_score=score, verdict_reasoning=reasoning)
        yield ReportComplete(report=report)

    # --- worker: NO database access ---
    def _compute_section(self, dim: Dimension, entity: EntityCard) -> DimensionOutcome:
        deps = self._deps
        results = retrieve_dimension(dim, entity, search=deps.search, today=deps.today)
        section = synthesize_section(dim, results, llm=deps.llm)
        return DimensionOutcome(section=section, raw_results=results)

    # --- driver-thread helpers (own the cache) ---
    def _resolve_entity_cached(self, vendor: str, cache: SQLiteCache) -> EntityCard:
        deps = self._deps
        name_key = _normalize_name(vendor)
        cached = cache.get(name_key, Dimension.SNAPSHOT)
        if cached is not None:
            return EntityCard.model_validate(cached)
        entity = resolve_entity(vendor, search=deps.search, llm=deps.llm)
        cache.put(name_key, Dimension.SNAPSHOT, entity.model_dump(mode="json"))
        return entity

    def _backlog_section(self, entity: EntityCard, cache: SQLiteCache, vendor_key: str,
                         news_positive_results: list[dict] | None) -> Section:
        cached = cache.get(vendor_key, Dimension.BACKLOG)
        if cached is not None:
            return Section.model_validate(cached)

        deps = self._deps
        results: list[dict] = []
        if entity.is_public and entity.ticker and entity.exchange:
            quote_url = build_quote_url(entity.exchange, entity.ticker)
            if quote_url:
                text, call_date = deps.fetch_transcript(quote_url)
                if text:
                    results = [{"title": "Earnings call transcript", "url": quote_url,
                                "content": text[:6000], "score": 1.0, "as_of": call_date}]
        if not results:  # private or no transcript -> reuse positive-news as a backlog proxy
            if news_positive_results is not None:
                results = news_positive_results
            else:
                results = retrieve_dimension(Dimension.NEWS_POSITIVE, entity,
                                             search=deps.search, today=deps.today)
        section = synthesize_section(Dimension.BACKLOG, results, llm=deps.llm)
        cache.put(vendor_key, Dimension.BACKLOG, section.model_dump(mode="json"), sources=results)
        return section


def run_report(vendor: str, deps: Deps) -> Report:
    """Backward-compatible entry point (CLI + Phase 1 tests). Sequential for deterministic order."""
    return ReportEngine(deps, mode="sequential").run_report(vendor)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_engine.py tests/test_pipeline.py -v`
Expected: PASS — the new engine tests AND all Phase 1 `test_pipeline.py` tests (the `run_report`/`Deps` surface is unchanged).

Then run the full suite: `cd backend && uv run pytest -v` — all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/pipeline.py backend/tests/test_engine.py
git commit -m "feat(engine): ReportEngine with parallel/sequential modes + streaming events"
```

---

## Task 4: CLI streams via `iter_events`

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/cli.py`
- Test: manual (CLI wiring; engine covered by unit tests). Do NOT run a live lookup (spends credits).

- [ ] **Step 1: Rewrite the CLI to consume the event stream**

Replace the entire contents of `backend/src/vendor_dd/surfaces/cli.py` with:

```python
# backend/src/vendor_dd/surfaces/cli.py
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vendor_dd.engine.backlog import fetch_transcript_text
from vendor_dd.engine.events import (
    EntityResolved, ReportComplete, SectionComplete, SectionError,
)
from vendor_dd.engine.llm import NebiusLLM
from vendor_dd.engine.pipeline import Deps, ReportEngine
from vendor_dd.engine.tavily_client import TavilySearchClient

# .env lives at the project root (one level above backend/): cli.py is
# backend/src/vendor_dd/surfaces/cli.py, so parents[4] == project root.
# (This corrects a latent Phase 1 path that pointed at backend/.env.)
load_dotenv(Path(__file__).resolve().parents[4] / ".env")
app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def main(vendor: str) -> None:
    """Generate a due-diligence report for VENDOR (streams sections as they complete)."""
    tavily_key, nebius_key = os.getenv("TAVILY_API_KEY"), os.getenv("NEBIUS_API_KEY")
    if not tavily_key or not nebius_key:
        console.print("[red]Set TAVILY_API_KEY and NEBIUS_API_KEY in .env[/red]")
        raise typer.Exit(1)

    deps = Deps(
        search=TavilySearchClient(tavily_key),
        llm=NebiusLLM(),
        cache_path=Path(".vendor_dd_cache.db"),
        today=date.today(),
        fetch_transcript=fetch_transcript_text,
    )
    engine = ReportEngine(deps, mode="parallel")

    report = None
    with console.status(f"Researching {vendor}..."):
        for ev in engine.iter_events(vendor):
            if isinstance(ev, EntityResolved):
                e = ev.entity
                console.print(Panel.fit(
                    f"[bold]{e.name}[/bold]  ·  {e.industry or '?'}  ·  {e.country or '?'}  "
                    f"·  {'public ' + (e.ticker or '') if e.is_public else 'private'}",
                    title="Entity", border_style="cyan"))
            elif isinstance(ev, SectionComplete):
                s = ev.section
                tag = " [dim](cached)[/dim]" if ev.cached else ""
                console.print(f"  ✓ {s.dimension.value}: {s.score}/10{tag}")
            elif isinstance(ev, SectionError):
                console.print(f"  [red]✗ {ev.dimension.value} failed: {ev.message}[/red]")
            elif isinstance(ev, ReportComplete):
                report = ev.report

    if report is None:
        console.print("[red]Report did not complete.[/red]")
        raise typer.Exit(1)

    console.print(Panel.fit(report.verdict_reasoning,
                            title=f"Verdict {report.verdict_score}/10", border_style="cyan"))

    table = Table("Dimension", "Score", "Reasoning")
    for s in sorted(report.sections, key=lambda s: s.score):  # riskiest first
        color = "green" if s.score >= 7 else "yellow" if s.score >= 4 else "red"
        table.add_row(s.dimension.value, f"[{color}]{s.score}/10[/{color}]", s.reasoning[:80])
    console.print(table)

    for s in report.sections:
        for f in s.findings:
            c = f.citation
            console.print(f"  [{s.dimension.value}] {f.claim}")
            console.print(f"     [dim]{c.url} · {c.source_type.value} · as of {c.as_of or 'n/a'}[/dim]")


if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Verify it imports and shows help (no live API call)**

Run: `cd backend && uv run python -c "from vendor_dd.surfaces.cli import app; print('ok')"`
Expected: `ok`

Run: `cd backend && uv run vendor-dd --help`
Expected: Typer help text with the `VENDOR` argument. (Does NOT invoke `main`'s body — no credits.)

- [ ] **Step 3: Confirm the suite still passes**

Run: `cd backend && uv run pytest -v`
Expected: all green (unchanged count).

- [ ] **Step 4: Commit**

```bash
git add backend/src/vendor_dd/surfaces/cli.py
git commit -m "feat(cli): stream sections live via ReportEngine.iter_events"
```

---

## Task 5: API request/response schemas

**Files:**
- Create: `backend/src/vendor_dd/surfaces/api/__init__.py` (empty)
- Create: `backend/src/vendor_dd/surfaces/api/schemas.py`
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_api.py
from vendor_dd.engine.schemas import Dimension
from vendor_dd.surfaces.api.schemas import (
    ProjectIn, VendorIn, VendorOut, DimensionScore, VendorSummary, ProjectDetail, VendorReport,
)


def test_schemas_construct():
    assert ProjectIn(name="Bridge job").name == "Bridge job"
    assert VendorIn(name="Cives Steel").name == "Cives Steel"
    v = VendorOut(id=1, project_id=2, name="Cives", vendor_key=None, created_at="2026-07-09")
    assert v.vendor_key is None
    ds = DimensionScore(dimension=Dimension.LEGAL, score=8, as_of="2026-07-09T00:00:00+00:00")
    summ = VendorSummary(vendor_id=1, name="Cives", vendor_key="cives.com", generated=True,
                         verdict_score=6, verdict_reasoning="ok", dimensions=[ds])
    assert ProjectDetail(id=1, name="p", created_at="t", vendors=[summ]).vendors[0].generated
    assert VendorReport(generated=False, vendor_key=None, entity=None, verdict_score=None,
                        verdict_reasoning=None, sections=[]).generated is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.surfaces.api.schemas`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/surfaces/api/__init__.py
```
(empty file)

```python
# backend/src/vendor_dd/surfaces/api/schemas.py
from __future__ import annotations

from pydantic import BaseModel

from vendor_dd.engine.schemas import Dimension, EntityCard, Section


class ProjectIn(BaseModel):
    name: str


class VendorIn(BaseModel):
    name: str


class ProjectOut(BaseModel):
    id: int
    name: str
    created_at: str


class VendorOut(BaseModel):
    id: int
    project_id: int
    name: str
    vendor_key: str | None
    created_at: str


class DimensionScore(BaseModel):
    dimension: Dimension
    score: int
    as_of: str  # ISO datetime (fetched_at)


class VendorSummary(BaseModel):
    vendor_id: int
    name: str
    vendor_key: str | None
    generated: bool
    verdict_score: int | None
    verdict_reasoning: str | None
    dimensions: list[DimensionScore]


class ProjectDetail(BaseModel):
    id: int
    name: str
    created_at: str
    vendors: list[VendorSummary]


class VendorReport(BaseModel):
    generated: bool
    vendor_key: str | None
    entity: EntityCard | None
    verdict_score: int | None
    verdict_reasoning: str | None
    sections: list[Section]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/__init__.py backend/src/vendor_dd/surfaces/api/schemas.py backend/tests/test_api.py
git commit -m "feat(api): request/response schemas"
```

---

## Task 6: Persistence store (projects / vendors)

**Files:**
- Create: `backend/src/vendor_dd/surfaces/api/store.py`
- Test: `backend/tests/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_store.py
from vendor_dd.surfaces.api.store import Store


def _store(tmp_path):
    return Store(tmp_path / "db.sqlite", clock=lambda: "2026-07-09T00:00:00+00:00")


def test_create_and_list_projects(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("Bridge job")
    assert p.id > 0 and p.name == "Bridge job" and p.created_at == "2026-07-09T00:00:00+00:00"
    assert [x.id for x in store.list_projects()] == [p.id]
    assert store.get_project(p.id).name == "Bridge job"
    assert store.get_project(9999) is None


def test_add_list_remove_vendors(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("Bridge job")
    v = store.add_vendor(p.id, "Cives Steel")
    assert v.project_id == p.id and v.name == "Cives Steel" and v.vendor_key is None
    assert [x.id for x in store.list_vendors(p.id)] == [v.id]
    assert store.get_vendor(v.id).name == "Cives Steel"
    store.remove_vendor(v.id)
    assert store.list_vendors(p.id) == []
    assert store.get_vendor(v.id) is None


def test_set_vendor_key_backfill(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("Bridge job")
    v = store.add_vendor(p.id, "Cives Steel")
    store.set_vendor_key(v.id, "cives.com")
    assert store.get_vendor(v.id).vendor_key == "cives.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.surfaces.api.store`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/surfaces/api/store.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Project:
    id: int
    name: str
    created_at: str


@dataclass
class Vendor:
    id: int
    project_id: int
    name: str
    vendor_key: str | None
    created_at: str


class Store:
    """Projects + vendors persistence. Shares its SQLite file with the report cache."""

    def __init__(self, path: str | Path, clock: Callable[[], str] = _utcnow_iso):
        self._clock = clock
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 name TEXT NOT NULL,
                 created_at TEXT NOT NULL
               )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS vendors (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 project_id INTEGER NOT NULL,
                 name TEXT NOT NULL,
                 vendor_key TEXT,
                 created_at TEXT NOT NULL,
                 FOREIGN KEY (project_id) REFERENCES projects(id)
               )"""
        )
        self._conn.commit()

    def create_project(self, name: str) -> Project:
        ts = self._clock()
        cur = self._conn.execute(
            "INSERT INTO projects (name, created_at) VALUES (?,?)", (name, ts))
        self._conn.commit()
        return Project(id=cur.lastrowid, name=name, created_at=ts)

    def list_projects(self) -> list[Project]:
        cur = self._conn.execute("SELECT id, name, created_at FROM projects ORDER BY id")
        return [Project(*row) for row in cur.fetchall()]

    def get_project(self, project_id: int) -> Project | None:
        cur = self._conn.execute(
            "SELECT id, name, created_at FROM projects WHERE id=?", (project_id,))
        row = cur.fetchone()
        return Project(*row) if row else None

    def add_vendor(self, project_id: int, name: str) -> Vendor:
        ts = self._clock()
        cur = self._conn.execute(
            "INSERT INTO vendors (project_id, name, vendor_key, created_at) VALUES (?,?,?,?)",
            (project_id, name, None, ts))
        self._conn.commit()
        return Vendor(id=cur.lastrowid, project_id=project_id, name=name,
                      vendor_key=None, created_at=ts)

    def list_vendors(self, project_id: int) -> list[Vendor]:
        cur = self._conn.execute(
            "SELECT id, project_id, name, vendor_key, created_at FROM vendors "
            "WHERE project_id=? ORDER BY id", (project_id,))
        return [Vendor(*row) for row in cur.fetchall()]

    def get_vendor(self, vendor_id: int) -> Vendor | None:
        cur = self._conn.execute(
            "SELECT id, project_id, name, vendor_key, created_at FROM vendors WHERE id=?",
            (vendor_id,))
        row = cur.fetchone()
        return Vendor(*row) if row else None

    def remove_vendor(self, vendor_id: int) -> None:
        self._conn.execute("DELETE FROM vendors WHERE id=?", (vendor_id,))
        self._conn.commit()

    def set_vendor_key(self, vendor_id: int, vendor_key: str) -> None:
        self._conn.execute(
            "UPDATE vendors SET vendor_key=? WHERE id=?", (vendor_key, vendor_id))
        self._conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_store.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/store.py backend/tests/test_store.py
git commit -m "feat(api): projects/vendors persistence store"
```

---

## Task 7: SSE frame mapper

**Files:**
- Create: `backend/src/vendor_dd/surfaces/api/sse.py`
- Test: `backend/tests/test_sse.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sse.py
import json
from vendor_dd.engine.schemas import Dimension, EntityCard, Section
from vendor_dd.engine.events import EntityResolved, SectionComplete, SectionError
from vendor_dd.surfaces.api.sse import to_sse_frame


def test_frame_has_event_name_and_json_data():
    ev = SectionComplete(section=Section(dimension=Dimension.LEGAL, findings=[],
                                         reasoning="ok", score=8), cached=True)
    frame = to_sse_frame(ev)
    assert frame["event"] == "section_complete"
    payload = json.loads(frame["data"])
    assert payload["section"]["dimension"] == "legal"
    assert payload["cached"] is True


def test_frame_event_name_matches_type_for_each_event():
    entity = EntityCard(name="Cives", domain="cives.com", is_public=False)
    assert to_sse_frame(EntityResolved(entity=entity))["event"] == "entity_resolved"
    assert to_sse_frame(SectionError(dimension=Dimension.FINANCIAL, message="x"))["event"] \
        == "section_error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_sse.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.surfaces.api.sse`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/surfaces/api/sse.py
from __future__ import annotations

from vendor_dd.engine.events import ReportEvent


def to_sse_frame(event: ReportEvent) -> dict[str, str]:
    """Map a ReportEvent to an sse-starlette frame: the event name + JSON payload.
    EventSourceResponse writes this as `event: <type>\\ndata: <json>\\n\\n`."""
    return {"event": event.type, "data": event.model_dump_json()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_sse.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/sse.py backend/tests/test_sse.py
git commit -m "feat(api): SSE frame mapper for report events"
```

---

## Task 8: App factory + REST routes (CRUD + read model)

**Files:**
- Create: `backend/src/vendor_dd/surfaces/api/routes.py`
- Create: `backend/src/vendor_dd/surfaces/api/app.py`
- Test: `backend/tests/test_api.py` (append)

- [ ] **Step 1: Write the failing test (append to test_api.py)**

```python
# append to backend/tests/test_api.py
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from vendor_dd.engine.schemas import EntityCard, Section, Dimension
from vendor_dd.engine.pipeline import Deps
from vendor_dd.surfaces.api.app import create_app


class FakeSearch:
    def search(self, **kwargs):
        return {"results": [{"title": "Cives Steel news", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


class FakeLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


def _client(tmp_path):
    deps = Deps(search=FakeSearch(), llm=FakeLLM(), cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    return TestClient(create_app(deps))


def test_project_and_vendor_crud(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Bridge job"}).json()["id"]
    assert any(p["id"] == pid for p in client.get("/projects").json())

    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    detail = client.get(f"/projects/{pid}").json()
    assert detail["vendors"][0]["vendor_id"] == vid
    assert detail["vendors"][0]["generated"] is False   # no report yet

    client.delete(f"/vendors/{vid}")
    assert client.get(f"/projects/{pid}").json()["vendors"] == []


def test_report_read_model_empty_before_generation(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is False and report["sections"] == []


def test_missing_project_and_vendor_return_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/projects/9999").status_code == 404
    assert client.get("/vendors/9999/report").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.surfaces.api.app`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/vendor_dd/surfaces/api/routes.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.schemas import Dimension, EntityCard, Section
from vendor_dd.engine.synthesis import assemble_verdict
from vendor_dd.surfaces.api.schemas import (
    DimensionScore, ProjectDetail, ProjectIn, ProjectOut, VendorIn, VendorOut,
    VendorReport, VendorSummary,
)
from vendor_dd.surfaces.api.store import Store, Vendor

router = APIRouter()


def _store(request: Request) -> Store:
    return request.app.state.store


def _cache(request: Request) -> SQLiteCache:
    return SQLiteCache(request.app.state.deps.cache_path)


def _report_sections(cache: SQLiteCache, vendor_key: str):
    """Parsed sections for a vendor (excludes the SNAPSHOT entity slot), keyed by dimension."""
    stored = cache.all_sections(vendor_key)
    return {d: (Section.model_validate(c), ts)
            for d, (c, ts) in stored.items() if d is not Dimension.SNAPSHOT}


def _summarize(vendor: Vendor, cache: SQLiteCache) -> VendorSummary:
    parsed = _report_sections(cache, vendor.vendor_key) if vendor.vendor_key else {}
    if not parsed:
        return VendorSummary(vendor_id=vendor.id, name=vendor.name, vendor_key=vendor.vendor_key,
                             generated=False, verdict_score=None, verdict_reasoning=None,
                             dimensions=[])
    sections = [sec for sec, _ in parsed.values()]
    score, reasoning = assemble_verdict(sections)
    dims = [DimensionScore(dimension=d, score=sec.score, as_of=ts.isoformat())
            for d, (sec, ts) in parsed.items()]
    return VendorSummary(vendor_id=vendor.id, name=vendor.name, vendor_key=vendor.vendor_key,
                         generated=True, verdict_score=score, verdict_reasoning=reasoning,
                         dimensions=dims)


@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectIn, request: Request):
    p = _store(request).create_project(body.name)
    return ProjectOut(id=p.id, name=p.name, created_at=p.created_at)


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(request: Request):
    return [ProjectOut(id=p.id, name=p.name, created_at=p.created_at)
            for p in _store(request).list_projects()]


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, request: Request):
    store = _store(request)
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    cache = _cache(request)
    vendors = [_summarize(v, cache) for v in store.list_vendors(project_id)]
    return ProjectDetail(id=project.id, name=project.name, created_at=project.created_at,
                         vendors=vendors)


@router.post("/projects/{project_id}/vendors", response_model=VendorOut)
def add_vendor(project_id: int, body: VendorIn, request: Request):
    store = _store(request)
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    v = store.add_vendor(project_id, body.name)
    return VendorOut(id=v.id, project_id=v.project_id, name=v.name,
                     vendor_key=v.vendor_key, created_at=v.created_at)


@router.delete("/vendors/{vendor_id}")
def remove_vendor(vendor_id: int, request: Request):
    _store(request).remove_vendor(vendor_id)
    return {"ok": True}


@router.get("/vendors/{vendor_id}/report", response_model=VendorReport)
def get_report(vendor_id: int, request: Request):
    store = _store(request)
    vendor = store.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    cache = _cache(request)
    parsed = _report_sections(cache, vendor.vendor_key) if vendor.vendor_key else {}
    if not parsed:
        return VendorReport(generated=False, vendor_key=vendor.vendor_key, entity=None,
                            verdict_score=None, verdict_reasoning=None, sections=[])
    sections = [sec for sec, _ in parsed.values()]
    score, reasoning = assemble_verdict(sections)
    entity_raw = cache.get(vendor.name.strip().lower(), Dimension.SNAPSHOT)
    entity = EntityCard.model_validate(entity_raw) if entity_raw else None
    return VendorReport(generated=True, vendor_key=vendor.vendor_key, entity=entity,
                        verdict_score=score, verdict_reasoning=reasoning, sections=sections)
```

```python
# backend/src/vendor_dd/surfaces/api/app.py
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vendor_dd.engine.pipeline import Deps
from vendor_dd.surfaces.api.routes import router
from vendor_dd.surfaces.api.store import Store

_DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(deps: Deps, *, cors_origins: list[str] | None = None) -> FastAPI:
    """Build the API around injected engine deps. Store + cache + engine share deps.cache_path."""
    app = FastAPI(title="Vendor Due-Diligence API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or _DEFAULT_ORIGINS,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.state.deps = deps
    app.state.store = Store(deps.cache_path)
    app.include_router(router)
    return app


def build_app() -> FastAPI:
    """Zero-arg production entrypoint (uvicorn --factory target). Reads keys from .env."""
    from dotenv import load_dotenv

    from vendor_dd.engine.backlog import fetch_transcript_text
    from vendor_dd.engine.llm import NebiusLLM
    from vendor_dd.engine.tavily_client import TavilySearchClient

    # app.py is backend/src/vendor_dd/surfaces/api/app.py, so parents[5] == project root
    # (where .env lives, one level above backend/).
    load_dotenv(Path(__file__).resolve().parents[5] / ".env")
    tavily_key, nebius_key = os.getenv("TAVILY_API_KEY"), os.getenv("NEBIUS_API_KEY")
    if not tavily_key or not nebius_key:
        raise RuntimeError("Set TAVILY_API_KEY and NEBIUS_API_KEY in .env")
    deps = Deps(
        search=TavilySearchClient(tavily_key),
        llm=NebiusLLM(),
        cache_path=Path(".vendor_dd_cache.db"),
        today=date.today(),
        fetch_transcript=fetch_transcript_text,
    )
    return create_app(deps)


def run() -> None:
    """`vendor-dd-api` script entry: serve build_app() with uvicorn."""
    import uvicorn
    uvicorn.run("vendor_dd.surfaces.api.app:build_app", factory=True, reload=True)
```

Note (design refinement vs. spec §4): `create_app` takes only `deps` and derives the SQLite path from `deps.cache_path`, rather than a separate `db_path` argument. This removes a two-parameters-must-match footgun — the store, cache, and engine are guaranteed to point at one file.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: PASS (CRUD + read-model + 404 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/routes.py backend/src/vendor_dd/surfaces/api/app.py backend/tests/test_api.py
git commit -m "feat(api): app factory + REST routes (projects/vendors CRUD + read model)"
```

---

## Task 9: SSE stream endpoint + packaging

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py` (add the stream endpoint)
- Modify: `backend/pyproject.toml` (add `sse-starlette`; add `vendor-dd-api` script)
- Test: `backend/tests/test_api.py` (append)

- [ ] **Step 1: Add `sse-starlette` and the script to pyproject.toml, then sync**

In `backend/pyproject.toml`, add to `[project].dependencies`: `"sse-starlette>=2.0.0"`.
In the existing `[project.scripts]` table, add: `vendor-dd-api = "vendor_dd.surfaces.api.app:run"`.

Run: `cd backend && uv sync --extra dev`
Expected: resolves and installs `sse-starlette`.

- [ ] **Step 2: Write the failing test (append to test_api.py)**

```python
# append to backend/tests/test_api.py
def _event_names(raw: str) -> list[str]:
    return [line[len("event:"):].strip()
            for line in raw.splitlines() if line.startswith("event:")]


def test_stream_endpoint_emits_full_event_sequence(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    names = _event_names(body)
    assert names[0] == "entity_resolved"
    assert names[-1] == "report_complete"
    assert names.count("section_complete") == 7   # 6 dims + backlog

    # vendor_key was backfilled during the stream, so the report is now readable
    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is True
    assert client.get(f"/projects/{pid}").json()["vendors"][0]["generated"] is True


def test_stream_missing_vendor_returns_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/vendors/9999/report/stream")
    assert resp.status_code == 404
```

- [ ] **Step 3: Add the stream endpoint to routes.py**

Add these imports at the top of `backend/src/vendor_dd/surfaces/api/routes.py` (alongside the existing imports):

```python
from sse_starlette.sse import EventSourceResponse

from vendor_dd.engine.events import EntityResolved
from vendor_dd.engine.pipeline import ReportEngine
from vendor_dd.surfaces.api.sse import to_sse_frame
```

Then append this endpoint to the same file (after `get_report`):

```python
@router.get("/vendors/{vendor_id}/report/stream")
def stream_report(vendor_id: int, request: Request):
    store = _store(request)
    vendor = store.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    engine = ReportEngine(request.app.state.deps, mode="parallel")

    def event_source():
        for ev in engine.iter_events(vendor.name):
            if isinstance(ev, EntityResolved):
                key = (ev.entity.domain or ev.entity.name).strip().lower()
                store.set_vendor_key(vendor_id, key)  # backfill so the read model can join
            yield to_sse_frame(ev)

    return EventSourceResponse(event_source())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: PASS (CRUD + read-model + stream tests). Then full suite: `cd backend && uv run pytest -v` — all green.

- [ ] **Step 5: Manual smoke (optional, spends credits — user-triggered)**

The API is now runnable against real keys:
```bash
cd backend && uv run vendor-dd-api          # serves on http://127.0.0.1:8000
# in another shell:
curl -s -X POST localhost:8000/projects -H 'content-type: application/json' -d '{"name":"demo"}'
curl -s -X POST localhost:8000/projects/1/vendors -H 'content-type: application/json' -d '{"name":"Boeing"}'
curl -N localhost:8000/vendors/1/report/stream     # watch events stream in (~13 credits)
```
Do NOT run this in CI or as part of the automated suite — it spends Tavily/Nebius credits. Reserve for a human-triggered demo.

- [ ] **Step 6: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/routes.py backend/pyproject.toml backend/tests/test_api.py backend/uv.lock
git commit -m "feat(api): SSE report stream endpoint + vendor-dd-api entrypoint"
```

---

## Phase 2 done — what's next (separate plans)

- **Phase 3:** React comparison-table frontend consuming `GET /projects/{id}` + `GET /vendors/{id}/report/stream`; chat over cached `sources`.
- **Phase 4:** Eval harness (gold set + citation-support judge + contamination rate) and LangSmith/OTel tracing.
- **Phase 5:** MCP server exposing `check_vendor(name)`.

---

## Self-review notes

- **Spec coverage:**
  - §2.1 one engine + `mode` param → Task 3 ✓ (`ReportEngine(mode=...)`, `max_workers=1` for sequential).
  - §2.2 algorithm (entity → dims → backlog → verdict, streaming) → Task 3 `iter_events` ✓.
  - §2.3 workers-touch-no-DB + per-run-local state → Task 3 (`_compute_section` has no cache access; executor + news-reuse are locals in `iter_events`) ✓.
  - §2.4 events → Task 1 ✓. §2.5 SectionError (dimension fails, report continues) → Task 3 ✓.
  - §3 persistence (projects/vendors, `sources` column, `vendor_key` backfill) → Tasks 2, 6, 9 ✓.
  - §3.2 `all_sections` TTL-ignoring read → Task 2 ✓.
  - §4.1 endpoints (all 7) → Tasks 8 (6 REST) + 9 (SSE) ✓.
  - §4.2 native `sse-starlette` sync generator → Tasks 7 (`to_sse_frame`) + 9 (`EventSourceResponse(event_source())`) ✓.
  - §5 tests (parity, event sequence, cache-hit, SectionError, store CRUD, sse mapper, api incl. SSE) → Tasks 1,2,3,5,6,7,8,9 ✓.
  - §6 deps (`sse-starlette`) + run command (`build_app` factory) → Task 9 + Task 8 ✓.
  - CLI streaming + SectionError (§8 file list) → Task 4 ✓.
- **Deferred (documented):** frontend, chat, evals, tracing, MCP, auth/migrations — out of Phase 2 per spec §7.
- **Deviation from spec:** `create_app(deps)` derives the db path from `deps.cache_path` instead of taking a separate `db_path` param (removes a must-match footgun). Noted in Task 8.
- **Type consistency:** `Deps`, `ReportEngine(mode=...)`, `iter_events`/`run_report`, `DimensionOutcome`, `to_sse_frame`, `Store`/`Project`/`Vendor`, `create_app`/`build_app`/`run`, event `type` discriminators used consistently across tasks. `run_report(vendor, deps)` module shim preserved so Phase 1 `test_pipeline.py` stays green.
- **No placeholders:** every code step is complete and runnable.
```
