"""vendor_dd — a vendor due-diligence engine powered by Tavily.

Architecture: one surface-agnostic `engine` (retrieval + cache + synthesis),
consumed by thin `surfaces` (CLI, FastAPI+SSE, MCP). Internals are stubbed until
the Tavily feasibility spike (backend/spikes/tavily_probe.py) confirms the pipeline shape.
"""
