from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from vendor_dd.engine.schemas import Dimension


@dataclass(frozen=True)
class DimensionConfig:
    query_template: str        # "{name} {geo} lawsuit litigation" — geo filled at runtime
    topic: str                 # "general" | "news" | "finance"
    search_depth: str          # "basic" | "advanced"
    max_results: int
    recency_days: int | None   # None = no time filter; else start_date = today - N days
    use_country: bool          # country param (general topic only)
    exact_match: bool          # wrap canonical name in quotes
    include_own_domain: bool   # certs: self-reported is the answer
    exclude_own_domain: bool   # independent dims: force third-party sources


DIMENSION_CONFIGS: dict[Dimension, DimensionConfig] = {
    Dimension.SNAPSHOT: DimensionConfig(
        "{name} company overview headquarters industry", "general", "advanced",
        5, None, True, False, False, False),
    Dimension.LEGAL: DimensionConfig(
        "{name} {geo} lawsuit litigation legal action", "general", "advanced",
        5, 730, True, True, False, True),
    Dimension.SAFETY: DimensionConfig(
        "{name} {geo} product recall safety defect investigation", "general", "advanced",
        5, 730, True, True, False, True),
    Dimension.FINANCIAL: DimensionConfig(
        "{name} {geo} layoffs bankruptcy financial trouble downgrade", "finance", "advanced",
        6, 365, False, True, False, True),
    Dimension.BACKLOG: DimensionConfig(
        "{name} {geo} backlog order book project pipeline", "finance", "advanced",
        5, 365, False, True, False, False),
    Dimension.CERTIFICATIONS: DimensionConfig(
        "{name} ISO AISC certification compliance quality", "general", "basic",
        3, None, True, False, True, False),
    Dimension.NEWS_POSITIVE: DimensionConfig(
        "{name} {geo} contract award partnership expansion", "news", "basic",
        8, 90, False, True, False, True),
    Dimension.NEWS_NEGATIVE: DimensionConfig(
        "{name} {geo} controversy incident dispute closure", "news", "basic",
        8, 90, False, True, False, True),
}

# TTL policy lives in code, not in the cache row (tunable without migration).
TTL: dict[Dimension, timedelta] = {
    Dimension.SNAPSHOT: timedelta(days=30),
    Dimension.CERTIFICATIONS: timedelta(days=30),
    Dimension.FINANCIAL: timedelta(days=7),
    Dimension.BACKLOG: timedelta(days=7),
    Dimension.LEGAL: timedelta(days=3),
    Dimension.SAFETY: timedelta(days=3),
    Dimension.NEWS_POSITIVE: timedelta(days=1),
    Dimension.NEWS_NEGATIVE: timedelta(days=1),
}

SCORE_THRESHOLD = 0.40  # drop Tavily results below this (anti-contamination)
