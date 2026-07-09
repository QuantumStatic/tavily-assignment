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
from vendor_dd.engine.schemas import Dimension, Report, Section
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


def run_report(vendor: str, deps: Deps) -> Report:
    entity = resolve_entity(vendor, search=deps.search, llm=deps.llm)
    vendor_key = (entity.domain or entity.name).strip().lower()
    cache = SQLiteCache(deps.cache_path)
    sections: list[Section] = []

    for dim in _TAVILY_DIMS:
        cached = cache.get(vendor_key, dim)
        if cached is not None:
            sections.append(Section.model_validate(cached))
            continue
        results = retrieve_dimension(dim, entity, search=deps.search, today=deps.today)
        section = synthesize_section(dim, results, llm=deps.llm)
        cache.put(vendor_key, dim, section.model_dump(mode="json"))
        sections.append(section)

    sections.append(_backlog_section(entity, deps, cache, vendor_key))

    score, reasoning = assemble_verdict(sections)
    return Report(vendor_input=vendor, entity=entity, sections=sections,
                  verdict_score=score, verdict_reasoning=reasoning)


def _backlog_section(entity, deps: Deps, cache: SQLiteCache, vendor_key: str) -> Section:
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
        results = retrieve_dimension(Dimension.NEWS_POSITIVE, entity,
                                     search=deps.search, today=deps.today)
    section = synthesize_section(Dimension.BACKLOG, results, llm=deps.llm)
    cache.put(vendor_key, Dimension.BACKLOG, section.model_dump(mode="json"))
    return section
