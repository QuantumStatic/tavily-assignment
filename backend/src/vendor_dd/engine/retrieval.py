from __future__ import annotations

from datetime import date
from typing import Any

from vendor_dd.engine.filtering import passes_filter
from vendor_dd.engine.schemas import Dimension, EntityCard
from vendor_dd.engine.tavily_client import SearchClient, build_search_kwargs


def retrieve_dimension(dim: Dimension, entity: EntityCard, *,
                       search: SearchClient, today: date) -> list[dict[str, Any]]:
    kwargs = build_search_kwargs(dim, entity, today=today)
    resp = search.search(**kwargs)
    return [r for r in resp.get("results", []) if passes_filter(r, entity.name)]
