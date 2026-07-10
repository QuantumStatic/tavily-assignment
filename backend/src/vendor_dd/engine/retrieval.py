from __future__ import annotations

from datetime import date
from typing import Any

from vendor_dd.engine.filtering import passes_filter
from vendor_dd.engine.schemas import Dimension, EntityCard
from vendor_dd.engine.tavily_client import SearchClient, build_search_kwargs
from vendor_dd.logs import get_logger

_LOG = get_logger("general")


def retrieve_dimension(dim: Dimension, entity: EntityCard, *,
                       search: SearchClient, today: date) -> list[dict[str, Any]]:
    kwargs = build_search_kwargs(dim, entity, today=today)
    resp = search.search(**kwargs)
    results = resp.get("results", [])
    # Filter on the common press name too — an article that says "Voith" would be
    # dropped if we required the full legal name to appear verbatim.
    match_name = entity.search_name or entity.name
    kept = [r for r in results if passes_filter(r, match_name)]
    _LOG.info("retrieval.filtered", extra={"payload": {
        "dimension": dim.value, "kept": len(kept), "filtered": len(results) - len(kept),
    }})
    return kept
