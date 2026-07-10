from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from vendor_dd.engine.schemas import Dimension


@dataclass(frozen=True)
class DimensionConfig:
    # Several FOCUSED queries per dimension instead of one keyword-stuffed query: each
    # single-concept query gets clean relevance, and retrieve_dimension pools + dedupes
    # their results. "{name}" is filled at runtime with the vendor's press name.
    query_templates: tuple[str, ...]
    topic: str                 # "general" | "news" | "finance"
    search_depth: str          # "basic" | "advanced" — basic for multi-query dims (cheaper)
    max_results: int           # PER query; the pool across queries is larger
    recency_days: int | None   # None = no time filter; else start_date = today - N days
    use_country: bool          # pass Tavily's country param (general topic only)
    exclude_own_domain: bool   # independent dims: force third-party sources


DIMENSION_CONFIGS: dict[Dimension, DimensionConfig] = {
    # SNAPSHOT/BACKLOG query_templates are unused (snapshot = entity resolution;
    # backlog = earnings-transcript / news reuse) but kept for a uniform config shape.
    Dimension.SNAPSHOT: DimensionConfig(
        ("{name} company overview",), "general", "basic",
        5, None, True, False),
    Dimension.LEGAL: DimensionConfig(
        ("{name} lawsuit", "{name} litigation", "{name} regulatory fine",
         "{name} investigation"),
        "general", "basic", 10, 730, True, True),
    Dimension.SAFETY: DimensionConfig(
        ("{name} safety incident", "{name} product recall", "{name} workplace accident",
         "{name} safety violation"),
        "general", "basic", 10, 730, True, True),
    # Own domain NOT excluded: financial figures are self-reported by nature — the
    # vendor's own audited report / IR page is the primary source of revenue/capex.
    # No recency window: Tavily's start_date zeroed out results (undated pages, and
    # the latest annual report is often >1yr old). The latest figures matter regardless.
    Dimension.FINANCIAL: DimensionConfig(
        ("{name} revenue", "{name} financial results", "{name} debt funding",
         "{name} profit"),
        "general", "basic", 10, None, False, False),
    Dimension.BACKLOG: DimensionConfig(
        ("{name} order backlog project pipeline",), "finance", "basic",
        10, 365, False, False),
    Dimension.CERTIFICATIONS: DimensionConfig(
        ("{name} ISO certification", "{name} quality certification", "{name} accreditation"),
        "general", "basic", 10, None, True, False),
    # Replaces the old news topic (which returned recency-broad noise). general topic
    # + focused queries; own domain included so the vendor's press releases count.
    # NO recency window: Tavily's start_date silently drops any result it can't date,
    # which for some vendors (e.g. Indian news sites) is ALL of them -> zero news.
    # Citations carry as_of dates where known, so recency info isn't lost.
    Dimension.NEWS: DimensionConfig(
        ("{name} news", "{name} contract award", "{name} expansion", "{name} controversy"),
        "general", "basic", 10, None, False, False),
}

# TTL policy lives in code, not in the cache row (tunable without migration).
TTL: dict[Dimension, timedelta] = {
    Dimension.SNAPSHOT: timedelta(days=30),
    Dimension.CERTIFICATIONS: timedelta(days=30),
    Dimension.FINANCIAL: timedelta(days=7),
    Dimension.BACKLOG: timedelta(days=7),
    Dimension.LEGAL: timedelta(days=3),
    Dimension.SAFETY: timedelta(days=3),
    Dimension.NEWS: timedelta(days=1),
}
