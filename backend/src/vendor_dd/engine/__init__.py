"""Surface-agnostic due-diligence engine.

Planned modules (filled after the feasibility spike):
  schemas.py    — Pydantic models: Report, Section, Citation, RiskFlag
  retrieval.py  — Tavily wrappers (search / extract), per-dimension queries
  synthesis.py  — LLM structured-output per section + overall risk summary
  pipeline.py   — orchestration: dimensions -> retrieve -> synthesize -> assemble
  cache.py      — SQLite TTL cache, per-section-type TTLs
"""
