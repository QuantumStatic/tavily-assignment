from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from vendor_dd.engine.schemas import Dimension

# Tavily relevance-score floors for the anti-contamination filter. general/finance
# topic scores run high (~0.4-0.9), but news-topic scores run an order of magnitude
# lower (~0.05-0.12) and don't even rank the right company first — a single global
# floor silently nukes every news hit. News therefore gates on entity-name presence
# (verify_entity) rather than score.
SCORE_THRESHOLD = 0.40
NEWS_SCORE_THRESHOLD = 0.0


@dataclass(frozen=True)
class DimensionConfig:
    query_template: str        # "{name} lawsuit litigation" — {name} filled at runtime
    topic: str                 # "general" | "news" | "finance"
    search_depth: str          # "basic" | "advanced"
    max_results: int
    recency_days: int | None   # None = no time filter; else start_date = today - N days
    use_country: bool          # pass Tavily's country param (general topic only)
    exact_match: bool          # wrap canonical name in quotes
    include_own_domain: bool   # certs: self-reported is the answer
    exclude_own_domain: bool   # independent dims: force third-party sources
    score_threshold: float = SCORE_THRESHOLD  # per-dim relevance floor (news overrides low)


DIMENSION_CONFIGS: dict[Dimension, DimensionConfig] = {
    Dimension.SNAPSHOT: DimensionConfig(
        "{name} company overview headquarters industry", "general", "advanced",
        5, None, True, False, False, False),
    Dimension.LEGAL: DimensionConfig(
        "{name} lawsuit litigation legal action", "general", "advanced",
        5, 730, True, True, False, True),
    Dimension.SAFETY: DimensionConfig(
        "{name} product recall safety defect investigation", "general", "advanced",
        5, 730, True, True, False, True),
    Dimension.FINANCIAL: DimensionConfig(
        "{name} layoffs bankruptcy financial trouble downgrade", "finance", "advanced",
        6, 365, False, True, False, True),
    Dimension.BACKLOG: DimensionConfig(
        "{name} backlog order book project pipeline", "finance", "advanced",
        5, 365, False, True, False, False),
    Dimension.CERTIFICATIONS: DimensionConfig(
        "{name} ISO AISC certification compliance quality", "general", "basic",
        3, None, True, False, True, False),
    Dimension.NEWS_POSITIVE: DimensionConfig(
        "{name} contract award partnership expansion", "news", "basic",
        8, 90, False, True, False, True, NEWS_SCORE_THRESHOLD),
    Dimension.NEWS_NEGATIVE: DimensionConfig(
        "{name} controversy incident dispute closure", "news", "basic",
        8, 90, False, True, False, True, NEWS_SCORE_THRESHOLD),
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
