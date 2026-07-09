from __future__ import annotations

from typing import Any

from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.schemas import Dimension, Section
from vendor_dd.prompts.synthesis import SECTION_SYNTHESIS_PROMPT


def synthesize_section(dim: Dimension, results: list[dict[str, Any]], *, llm: LLMClient) -> Section:
    rendered = "\n".join(
        f"- score={r.get('score')} | {r.get('title','')} | {r.get('url','')} | {r.get('content','')}"
        for r in results
    ) or "(no results found)"
    section = llm.structured(SECTION_SYNTHESIS_PROMPT.format(dimension=dim.value, results=rendered), Section)
    # The LLM's structured output isn't guaranteed to echo back the exact dimension we asked it
    # to write about (e.g. loosely-prompted models may reuse a single sample section verbatim).
    # Force it to the dimension we actually requested so section identity is deterministic.
    if section.dimension is not dim:
        section = section.model_copy(update={"dimension": dim})
    return section


def assemble_verdict(sections: list[Section]) -> tuple[int, str]:
    if not sections:
        return 5, "Insufficient data to assess."
    avg = round(sum(s.score for s in sections) / len(sections))
    weakest = min(sections, key=lambda s: s.score)
    reasoning = (
        f"Overall {avg}/10. Weakest area: {weakest.dimension.value} "
        f"({weakest.score}/10) — {weakest.reasoning}"
    )
    return avg, reasoning
