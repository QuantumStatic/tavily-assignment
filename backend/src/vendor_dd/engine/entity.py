from __future__ import annotations

from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.schemas import EntityCard
from vendor_dd.engine.tavily_client import SearchClient
from vendor_dd.prompts.entity import ENTITY_RESOLUTION_PROMPT


def resolve_entity(name: str, *, search: SearchClient, llm: LLMClient) -> EntityCard:
    resp = search.search(query=f"{name} company overview headquarters industry ticker",
                         topic="general", search_depth="advanced", max_results=5)
    results = "\n".join(
        f"- {r.get('title','')}: {r.get('content','')} ({r.get('url','')})"
        for r in resp.get("results", [])
    )
    return llm.structured(ENTITY_RESOLUTION_PROMPT.format(name=name, results=results), EntityCard)
