from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol

from tavily import TavilyClient

from vendor_dd.engine.config import DIMENSION_CONFIGS
from vendor_dd.engine.schemas import Dimension, EntityCard


class SearchClient(Protocol):
    def search(self, **kwargs: Any) -> dict[str, Any]: ...


def build_search_kwargs(dim: Dimension, entity: EntityCard, *, today: date) -> dict[str, Any]:
    """Translate a dimension + entity card into Tavily search params (docs-backed)."""
    cfg = DIMENSION_CONFIGS[dim]
    geo = "" if cfg.use_country else (entity.country or "")  # geo in query only when country unusable
    name = f'"{entity.name}"' if cfg.exact_match else entity.name
    query = cfg.query_template.format(name=name, geo=geo).replace("  ", " ").strip()

    kwargs: dict[str, Any] = {
        "query": query,
        "topic": cfg.topic,
        "search_depth": cfg.search_depth,
        "max_results": cfg.max_results,
    }
    if cfg.use_country and entity.country:
        kwargs["country"] = entity.country
    if cfg.recency_days is not None:
        kwargs["start_date"] = (today - timedelta(days=cfg.recency_days)).isoformat()
    if cfg.exclude_own_domain and entity.domain:
        kwargs["exclude_domains"] = [entity.domain]
    if cfg.include_own_domain and entity.domain:
        kwargs["include_domains"] = [entity.domain]
    return kwargs


class TavilySearchClient:
    """Thin wrapper implementing SearchClient over tavily-python."""

    def __init__(self, api_key: str):
        self._client = TavilyClient(api_key=api_key)

    def search(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.search(**kwargs)
