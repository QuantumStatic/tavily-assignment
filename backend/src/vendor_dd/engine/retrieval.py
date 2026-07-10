from __future__ import annotations

from datetime import date
from typing import Any

from vendor_dd.engine.config import DIMENSION_CONFIGS
from vendor_dd.engine.filtering import passes_filter
from vendor_dd.engine.schemas import Dimension, EntityCard
from vendor_dd.engine.tavily_client import SearchClient, build_search_kwargs
from vendor_dd.logs import get_logger

_LOG = get_logger("general")


def retrieve_dimension(dim: Dimension, entity: EntityCard, *,
                       search: SearchClient, today: date) -> list[dict[str, Any]]:
    """Run each of the dimension's focused queries, then pool + dedupe (by URL) + filter.
    Several single-concept queries beat one keyword-stuffed query: each gets clean
    relevance, and together they cover more ground."""
    cfg = DIMENSION_CONFIGS[dim]
    # Filter on the common press name too — an article that says "Voith" would be dropped
    # if we required the full legal name to appear verbatim.
    match_name = entity.search_name or entity.name
    pooled: dict[str, dict[str, Any]] = {}
    raw = 0
    for template in cfg.query_templates:
        kwargs = build_search_kwargs(dim, entity, today=today, query_template=template)
        results = search.search(**kwargs).get("results", [])
        raw += len(results)
        for r in results:
            if not passes_filter(r, match_name):
                continue
            key = r.get("url") or f"_nourl_{len(pooled)}"
            pooled.setdefault(key, r)   # first occurrence wins (dedupe across queries)
    kept = list(pooled.values())
    _LOG.info("retrieval.filtered", extra={"payload": {
        "dimension": dim.value, "queries": len(cfg.query_templates),
        "raw": raw, "kept": len(kept), "dropped": raw - len(kept),
    }})
    return kept
