ENTITY_RESOLUTION_PROMPT = """Given these web search results about a company called "{name}", extract its
canonical identity. Set is_public=true only if it is publicly traded; if so include ticker
and exchange (e.g. "NYSE"), else ticker/exchange null.

`name` is the full legal name (e.g. "Voith Hydro Holding GmbH & Co. KG").
`search_name` is the short, common name journalists and the public actually use in news
coverage (e.g. "Voith Hydro", or just "Voith"). Strip legal suffixes like GmbH, Co. KG, Inc,
LLC, Ltd, Holding, Group. Use the brand a reporter would write, NOT the registered legal name —
nobody writes the full legal name in an article. When in doubt, prefer the shorter distinctive
brand name.

Search results:
{results}
"""
