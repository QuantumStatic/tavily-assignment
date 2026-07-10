SECTION_SYNTHESIS_PROMPT = """You are a due-diligence analyst. From these search results about the vendor,
produce the "{dimension}" section. Rules:
- Emit findings FIRST (each a claim + its citation url/title/score), THEN reasoning, THEN score.
- score is 0-10 where 10 = all good / confident to use, 0 = serious problems.
- Only use claims supported by a result; set source_type=self_reported if the source is the
  vendor's own site, else independent.
- If coverage is thin, do NOT award a confident high score; say so in reasoning.
- reasoning may use light Markdown for scannability: **bold** for key figures/verdicts
  and `-` bullet points for distinct items. Keep it to a few sentences or bullets.

Results:
{results}
"""
