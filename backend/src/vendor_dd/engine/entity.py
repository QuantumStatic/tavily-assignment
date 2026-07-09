from __future__ import annotations

from vendor_dd.engine.llm import LLMClient
from vendor_dd.engine.schemas import EntityCard
from vendor_dd.engine.tavily_client import SearchClient

_PROMPT = """Given these web search results about a company called "{name}", extract its
canonical identity. Set is_public=true only if it is publicly traded; if so include ticker
and exchange (e.g. "NYSE"), else ticker/exchange null.

Search results:
{results}
"""


def resolve_entity(name: str, *, search: SearchClient, llm: LLMClient) -> EntityCard:
    resp = search.search(query=f"{name} company overview headquarters industry ticker",
                         topic="general", search_depth="advanced", max_results=5)
    results = "\n".join(
        f"- {r.get('title','')}: {r.get('content','')} ({r.get('url','')})"
        for r in resp.get("results", [])
    )
    return llm.structured(_PROMPT.format(name=name, results=results), EntityCard)
