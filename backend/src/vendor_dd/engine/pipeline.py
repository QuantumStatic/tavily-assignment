from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterator, Literal

from vendor_dd.engine.backlog import build_quote_url
from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.entity import resolve_entity
from vendor_dd.engine.events import (
    EntityResolved, ReportComplete, ReportError, ReportEvent, SectionComplete, SectionError,
)
from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.retrieval import retrieve_dimension
from vendor_dd.engine.schemas import Dimension, EntityCard, Report, Section
from vendor_dd.engine.synthesis import assemble_verdict, synthesize_section
from vendor_dd.engine.tavily_client import SearchClient
from vendor_dd.logs import get_logger

_LOG = get_logger("general")

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


class _SessionSearch:
    """Wraps a SearchClient to stamp run-level tracing headers (Tavily X-Session-Id /
    X-Client-Name) onto every search. Transparent: setdefault never overrides an explicit
    kwarg, and a call site that passes nothing extra behaves exactly as the inner client."""

    def __init__(self, inner: SearchClient, session_id: str):
        self._inner = inner
        self._session_id = session_id

    def search(self, **kwargs):
        kwargs.setdefault("session_id", self._session_id)
        kwargs.setdefault("client_name", "vendor-dd")
        return self._inner.search(**kwargs)


class ReportEngine:
    """Runs a due-diligence report. `mode` selects concurrency; the algorithm is identical.

    - mode="parallel"   -> ThreadPoolExecutor(max_workers) fan-out (streams out-of-order)
    - mode="sequential" -> single worker (deterministic completion order)

    Threads (not async) because the work is I/O-bound. Worker threads do ONLY pure
    retrieve+synthesize; the driver thread owns every SQLite read/write.
    """

    def __init__(self, deps: Deps, *, mode: Literal["parallel", "sequential"] = "parallel",
                 max_workers: int = 6, session_id: str | None = None):
        self._deps = deps
        self._max_workers = 1 if mode == "sequential" else max_workers
        self._search: SearchClient = (
            _SessionSearch(deps.search, session_id) if session_id else deps.search)

    def run_report(self, vendor: str) -> Report:
        report: Report | None = None
        for ev in self.iter_events(vendor):
            if isinstance(ev, ReportComplete):
                report = ev.report
        assert report is not None, "iter_events must end with ReportComplete"
        return report

    def iter_events(self, vendor: str) -> Iterator[ReportEvent]:
        deps = self._deps
        Path(deps.cache_path).parent.mkdir(parents=True, exist_ok=True)
        cache = SQLiteCache(deps.cache_path)
        _LOG.info("report.start", extra={"payload": {"vendor": vendor, "max_workers": self._max_workers}})
        try:
            try:
                entity = self._resolve_entity_cached(vendor, cache)
            except Exception as exc:  # fatal: no entity to build a report around
                _LOG.error("report.error", extra={"payload": {"stage": "entity", "error": str(exc)}})
                yield ReportError(message="report could not be generated")
                return
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
                    ctx = contextvars.copy_context()
                    pending[pool.submit(ctx.run, self._compute_section, dim, entity)] = dim

                for fut in as_completed(pending):
                    dim = pending[fut]
                    try:
                        outcome = fut.result()
                    except Exception as exc:  # one dimension failed; the report goes on without it
                        _LOG.error("section.error", extra={"payload": {"dimension": dim.value, "error": str(exc)}})
                        yield SectionError(dimension=dim, message=f"{dim.value} lookup failed")
                        continue
                    cache.put(vendor_key, dim, outcome.section.model_dump(mode="json"),
                              sources=outcome.raw_results)
                    if dim is Dimension.NEWS_POSITIVE:
                        news_positive_results = outcome.raw_results
                    sections.append(outcome.section)
                    yield SectionComplete(section=outcome.section, cached=False)

            try:
                backlog, backlog_cached = self._backlog_section(entity, cache, vendor_key,
                                                                 news_positive_results)
            except Exception as exc:  # fatal: report would be incomplete without backlog
                _LOG.error("report.error", extra={"payload": {"stage": "backlog", "error": str(exc)}})
                yield ReportError(message="report could not be generated")
                return
            sections.append(backlog)
            yield SectionComplete(section=backlog, cached=backlog_cached)

            score, reasoning = assemble_verdict(sections)
            report = Report(vendor_input=vendor, entity=entity, sections=sections,
                            verdict_score=score, verdict_reasoning=reasoning)
            _LOG.info("report.complete", extra={"payload": {"vendor": vendor, "score": score,
                                                             "sections": len(sections)}})
            yield ReportComplete(report=report)
        finally:
            cache.close()

    # --- worker: NO database access ---
    def _compute_section(self, dim: Dimension, entity: EntityCard) -> DimensionOutcome:
        deps = self._deps
        results = retrieve_dimension(dim, entity, search=self._search, today=deps.today)
        section = synthesize_section(dim, results, llm=deps.llm)
        _LOG.info("section.computed", extra={"payload": {"dimension": dim.value, "score": section.score}})
        return DimensionOutcome(section=section, raw_results=results)

    # --- driver-thread helpers (own the cache) ---
    def _resolve_entity_cached(self, vendor: str, cache: SQLiteCache) -> EntityCard:
        """Entity resolution is cached by the raw (normalized) input name, since the
        resolved domain isn't known until after resolution runs. Reusing Dimension.SNAPSHOT
        as the cache slot: its DimensionConfig/TTL entries already exist but are otherwise
        unused (SNAPSHOT is not a Tavily-retrieved report section)."""
        deps = self._deps
        name_key = _normalize_name(vendor)
        cached = cache.get(name_key, Dimension.SNAPSHOT)
        if cached is not None:
            return EntityCard.model_validate(cached)
        entity = resolve_entity(vendor, search=self._search, llm=deps.llm)
        cache.put(name_key, Dimension.SNAPSHOT, entity.model_dump(mode="json"))
        return entity

    def _backlog_section(self, entity: EntityCard, cache: SQLiteCache, vendor_key: str,
                         news_positive_results: list[dict] | None) -> tuple[Section, bool]:
        cached = cache.get(vendor_key, Dimension.BACKLOG)
        if cached is not None:
            return Section.model_validate(cached), True

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
                # NEWS_POSITIVE was freshly retrieved this run (not a cache hit) -> reuse
                # those raw results instead of paying for a second, identical Tavily call.
                results = news_positive_results
            else:
                # NEWS_POSITIVE was served from cache this run, so there are no fresh raw
                # results to reuse. This is a rarer path (news has a 1-day TTL, so it's
                # usually stale/refetched), so falling back to a live call here is acceptable.
                results = retrieve_dimension(Dimension.NEWS_POSITIVE, entity,
                                             search=self._search, today=deps.today)
        section = synthesize_section(Dimension.BACKLOG, results, llm=deps.llm)
        cache.put(vendor_key, Dimension.BACKLOG, section.model_dump(mode="json"), sources=results)
        return section, False


def run_report(vendor: str, deps: Deps) -> Report:
    """Backward-compatible entry point (CLI + Phase 1 tests). Sequential for deterministic order."""
    return ReportEngine(deps, mode="sequential").run_report(vendor)
