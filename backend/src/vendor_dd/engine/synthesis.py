from __future__ import annotations

from typing import Any

from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.schemas import Dimension, Section

_PROMPT = """You are a due-diligence analyst. From these search results about the vendor,
produce the "{dimension}" section. Rules:
- Emit findings FIRST (each a claim + its citation url/title/score), THEN reasoning, THEN score.
- score is 0-10 where 10 = all good / confident to use, 0 = serious problems.
- Only use claims supported by a result; set source_type=self_reported if the source is the
  vendor's own site, else independent.
- If coverage is thin, do NOT award a confident high score; say so in reasoning.

Results:
{results}
"""


def synthesize_section(dim: Dimension, results: list[dict[str, Any]], *, llm: LLMClient) -> Section:
    rendered = "\n".join(
        f"- score={r.get('score')} | {r.get('title','')} | {r.get('url','')} | {r.get('content','')}"
        for r in results
    ) or "(no results found)"
    return llm.structured(_PROMPT.format(dimension=dim.value, results=rendered), Section)


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
