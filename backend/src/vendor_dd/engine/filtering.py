from __future__ import annotations

import re
from typing import Any

from vendor_dd.engine.config import SCORE_THRESHOLD

# Legal-entity tokens that appear in a registered name but almost never in news
# coverage ("Voith GmbH" in the filings, "Voith" in the headlines). Stripped before
# the name-presence check so a suffix on the resolved name doesn't reject real hits.
_LEGAL_SUFFIXES = {
    "gmbh", "ag", "se", "kg", "kgaa", "co", "inc", "llc", "ltd", "limited",
    "corp", "corporation", "company", "plc", "sa", "nv", "bv", "oyj", "ab",
    "holding", "holdings", "group", "gruppe", "the", "and",
}


def _core_tokens(name: str) -> list[str]:
    """Distinctive brand tokens of a company name, sans legal suffixes/punctuation.
    'Voith GmbH' -> ['voith']; 'Voith Hydro Holding GmbH & Co. KG' -> ['voith','hydro']."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
    core = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return core or tokens[:1]   # never empty: fall back to the first token


def verify_entity(result: dict[str, Any], entity_name: str) -> bool:
    """True if the result's title or content actually names the entity. Matches on the
    core brand tokens (order-independent) so a legal suffix on the resolved name — or a
    stray word in the article — doesn't reject a genuine mention."""
    hay = f"{result.get('title', '')} {result.get('content', '')}".lower()
    tokens = _core_tokens(entity_name)
    return all(tok in hay for tok in tokens) if tokens else True


def passes_filter(result: dict[str, Any], entity_name: str,
                  threshold: float = SCORE_THRESHOLD) -> bool:
    """Anti-contamination gate: drop weak scores and wrong-company results."""
    if result.get("score", 0.0) < threshold:
        return False
    return verify_entity(result, entity_name)
