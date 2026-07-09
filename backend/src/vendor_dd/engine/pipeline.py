from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from vendor_dd.engine.backlog import build_quote_url
from vendor_dd.engine.cache import SQLiteCache
from vendor_dd.engine.entity import resolve_entity
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


def _normalize_name(name: str) -> str:
    return name.strip().lower()


def _resolve_entity_cached(vendor: str, deps: Deps, cache: SQLiteCache) -> EntityCard:
    """Entity resolution is cached by the raw (normalized) input name, since the
    resolved domain isn't known until after resolution runs. Reusing Dimension.SNAPSHOT
    as the cache slot: its DimensionConfig/TTL entries already exist but are otherwise
    unused (SNAPSHOT is not a Tavily-retrieved report section)."""
    name_key = _normalize_name(vendor)
    cached = cache.get(name_key, Dimension.SNAPSHOT)
    if cached is not None:
        return EntityCard.model_validate(cached)

    entity = resolve_entity(vendor, search=deps.search, llm=deps.llm)
    cache.put(name_key, Dimension.SNAPSHOT, entity.model_dump(mode="json"))
    return entity


def run_report(vendor: str, deps: Deps) -> Report:
    cache = SQLiteCache(deps.cache_path)
    entity = _resolve_entity_cached(vendor, deps, cache)
    vendor_key = (entity.domain or entity.name).strip().lower()
    sections: list[Section] = []
    news_positive_results: list[dict] | None = None

    for dim in _TAVILY_DIMS:
        cached = cache.get(vendor_key, dim)
        if cached is not None:
            sections.append(Section.model_validate(cached))
            continue
        results = retrieve_dimension(dim, entity, search=deps.search, today=deps.today)
        if dim is Dimension.NEWS_POSITIVE:
            news_positive_results = results
        section = synthesize_section(dim, results, llm=deps.llm)
        cache.put(vendor_key, dim, section.model_dump(mode="json"))
        sections.append(section)

    sections.append(_backlog_section(entity, deps, cache, vendor_key, news_positive_results))

    score, reasoning = assemble_verdict(sections)
    return Report(vendor_input=vendor, entity=entity, sections=sections,
                  verdict_score=score, verdict_reasoning=reasoning)


def _backlog_section(entity, deps: Deps, cache: SQLiteCache, vendor_key: str,
                      news_positive_results: list[dict] | None) -> Section:
    cached = cache.get(vendor_key, Dimension.BACKLOG)
    if cached is not None:
        return Section.model_validate(cached)

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
                                         search=deps.search, today=deps.today)
    section = synthesize_section(Dimension.BACKLOG, results, llm=deps.llm)
    cache.put(vendor_key, Dimension.BACKLOG, section.model_dump(mode="json"))
    return section
