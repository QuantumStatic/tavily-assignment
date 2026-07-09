ENTITY_RESOLUTION_PROMPT = """Given these web search results about a company called "{name}", extract its
canonical identity. Set is_public=true only if it is publicly traded; if so include ticker
and exchange (e.g. "NYSE"), else ticker/exchange null.

Search results:
{results}
"""
