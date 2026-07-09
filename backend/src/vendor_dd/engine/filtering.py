from __future__ import annotations

from typing import Any

from vendor_dd.engine.config import SCORE_THRESHOLD


def verify_entity(result: dict[str, Any], entity_name: str) -> bool:
    """True if the result's title or content actually names the entity (case-insensitive)."""
    hay = f"{result.get('title', '')} {result.get('content', '')}".lower()
    return entity_name.lower() in hay


def passes_filter(result: dict[str, Any], entity_name: str,
                  threshold: float = SCORE_THRESHOLD) -> bool:
    """Anti-contamination gate: drop weak scores and wrong-company results."""
    if result.get("score", 0.0) < threshold:
        return False
    return verify_entity(result, entity_name)
