from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from vendor_dd.engine.schemas import Dimension


@dataclass(frozen=True)
class DimensionConfig:
    query_template: str        # "{name} lawsuit litigation" — {name} filled at runtime
    topic: str                 # "general" | "news" | "finance"
    search_depth: str          # "basic" | "advanced"
    max_results: int
    recency_days: int | None   # None = no time filter; else start_date = today - N days
    use_country: bool          # pass Tavily's country param (general topic only)
    exclude_own_domain: bool   # independent dims: force third-party sources


DIMENSION_CONFIGS: dict[Dimension, DimensionConfig] = {
    Dimension.SNAPSHOT: DimensionConfig(
        "{name} company overview headquarters industry", "general", "advanced",
        5, None, True, False),
    Dimension.LEGAL: DimensionConfig(
        "{name} lawsuit litigation legal action", "general", "advanced",
        20, 730, True, True),
    Dimension.SAFETY: DimensionConfig(
        "{name} product recall safety defect investigation", "general", "advanced",
        20, 730, True, True),
    # finance topic already scopes to financial coverage — no keyword stuffing needed.
    Dimension.FINANCIAL: DimensionConfig(
        "{name}", "finance", "advanced",
        20, 365, False, True),
    Dimension.BACKLOG: DimensionConfig(
        "{name} backlog order book project pipeline", "finance", "advanced",
        20, 365, False, False),
    # No domain restriction: certs can come from the vendor's own site OR independent
    # registrar/registry listings (an independent listing is stronger corroboration).
    Dimension.CERTIFICATIONS: DimensionConfig(
        "{name} ISO AISC certification compliance quality", "general", "advanced",
        20, None, True, False),
    # NOT topic="news": Tavily's news topic returns recency-broad noise for a
    # low-coverage company (verified: 0-1/20 mention the vendor). The general topic
    # does real keyword relevance — "{name} news" returns 19/20 on-topic. One news
    # section; the LLM weighs positive vs adverse coverage (no +/- query split).
    # Own domain NOT excluded: for a private vendor, its own press releases are a
    # legitimate news source. 120-day window.
    Dimension.NEWS: DimensionConfig(
        "{name} news", "general", "advanced",
        20, 120, False, False),
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
