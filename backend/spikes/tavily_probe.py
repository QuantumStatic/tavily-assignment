# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tavily-python>=0.5.0",
#   "python-dotenv>=1.0.0",
#   "rich>=13.0.0",
# ]
# ///
"""
Feasibility spike #1 — does Tavily return usable material for vendor due diligence?

Run BEFORE building anything. It hits Tavily search directly (raw results, no LLM)
across the risk dimensions our product would use, so we can eyeball quality,
coverage (public vs private companies), and whether snippets are enough or we
need the Extract API.

Usage:
    # put TAVILY_API_KEY in ../../.env (or your shell), then:
    uv run tavily_probe.py "Boeing"
    uv run tavily_probe.py "Acme Regional HVAC Supply"   # try an obscure private co
    uv run tavily_probe.py "Boeing" --depth basic        # compare basic vs advanced
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.rule import Rule
from tavily import TavilyClient

# Load .env from the project root (two levels up from backend/spikes/)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

console = Console()

# The risk dimensions our due-diligence report is built from. Each becomes a
# targeted query. If these come back empty/irrelevant for a real vendor, the
# whole "cited risk sections" premise needs rethinking.
RISK_DIMENSIONS: dict[str, str] = {
    "legal":          '{v} lawsuit litigation legal action',
    "recall_safety":  '{v} product recall safety defect investigation',
    "financial":      '{v} layoffs bankruptcy financial trouble downgrade',
    "certifications": '{v} ISO certification compliance quality',
    "recent_news":    '{v} news partnership contract award',
}


def probe(vendor: str, depth: str = "advanced", max_results: int = 5) -> None:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        console.print("[bold red]Missing TAVILY_API_KEY[/bold red] — add it to .env or your shell.")
        raise SystemExit(1)

    client = TavilyClient(api_key=key)
    console.print(f"\n[bold cyan]Vendor:[/bold cyan] {vendor}   [dim](depth={depth})[/dim]")

    for dimension, template in RISK_DIMENSIONS.items():
        query = template.format(v=vendor)
        console.print(Rule(f"[yellow]{dimension}[/yellow]  ·  {query}"))
        try:
            resp = client.search(query=query, search_depth=depth, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 — spike: surface any API error plainly
            console.print(f"  [red]search failed:[/red] {exc}")
            continue

        results = resp.get("results", [])
        if not results:
            console.print("  [red]— no results —[/red]  (coverage gap for this vendor/dimension)")
            continue

        for i, r in enumerate(results, 1):
            score = r.get("score", 0.0)
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            snippet = " ".join((r.get("content") or "").split())
            console.print(f"  [bold]{i}.[/bold] [green]{score:.2f}[/green]  {title}")
            console.print(f"     [dim]{url}[/dim]")
            console.print(f"     snippet[{len(snippet)} chars]: {snippet[:200]}{'...' if len(snippet) > 200 else ''}")
        console.print()

    console.print(
        "[bold]What to check:[/bold] relevance + recency of top hits, whether snippets "
        "support a claim on their own (or we need Extract), and coverage on the obscure "
        "private vendor vs the big public one."
    )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    depth = "basic" if "--depth" in sys.argv and "basic" in sys.argv else "advanced"
    vendor = args[0] if args else "Boeing"
    probe(vendor, depth=depth)
