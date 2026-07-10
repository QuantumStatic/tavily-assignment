from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vendor_dd.engine.pipeline import Deps
from vendor_dd.surfaces.api.routes import router
from vendor_dd.surfaces.api.store import Store

_DEFAULT_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(deps: Deps, *, cors_origins: list[str] | None = None) -> FastAPI:
    """Build the API around injected engine deps. Store + cache + engine share deps.cache_path."""
    app = FastAPI(title="Vendor Due-Diligence API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or _DEFAULT_ORIGINS,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.state.deps = deps
    app.state.store = Store(deps.cache_path)
    app.include_router(router)
    return app


def build_app() -> FastAPI:
    """Zero-arg production entrypoint (uvicorn --factory target). Reads keys from .env."""
    from dotenv import load_dotenv

    from vendor_dd.engine.backlog import fetch_transcript_text
    from vendor_dd.engine.llm import NebiusLLM
    from vendor_dd.engine.tavily_client import TavilySearchClient

    # app.py is backend/src/vendor_dd/surfaces/api/app.py, so parents[5] == project root
    # (where .env lives, one level above backend/).
    load_dotenv(Path(__file__).resolve().parents[5] / ".env")
    tavily_key, nebius_key = os.getenv("TAVILY_API_KEY"), os.getenv("NEBIUS_API_KEY")
    if not tavily_key or not nebius_key:
        raise RuntimeError("Set TAVILY_API_KEY and NEBIUS_API_KEY in .env")
    deps = Deps(
        search=TavilySearchClient(tavily_key),
        llm=NebiusLLM(),
        cache_path=Path(".vendor_dd_cache.db"),
        today=date.today(),
        fetch_transcript=fetch_transcript_text,
    )
    return create_app(deps)


def run() -> None:
    """`vendor-dd-api` script entry: serve build_app() with uvicorn."""
    import uvicorn
    uvicorn.run("vendor_dd.surfaces.api.app:build_app", factory=True, reload=True)
