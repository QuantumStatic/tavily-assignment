"""vendor_dd — a vendor due-diligence engine powered by Tavily.

Architecture: one surface-agnostic `engine` (retrieval + cache + synthesis), consumed
by thin `surfaces` — a `vendor-dd` CLI and a `vendor-dd-api` FastAPI + SSE server.
"""

__version__ = "0.1.0"
