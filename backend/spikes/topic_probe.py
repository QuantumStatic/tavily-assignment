# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tavily-python>=0.5.0",
#   "python-dotenv>=1.0.0",
#   "rich>=13.0.0",
# ]
# ///
"""
Feasibility spike #2 — confirm topic and recency params behave as the design assumes.

Tests:
  1. topic="finance" vs topic="general" for a financial/backlog query (is finance better sourced?)
  2. topic="news" with a runtime-computed 90-day start_date (do we get RECENT news?)
  3. country param on a general query (does the geo boost work?)

Usage:
    uv run topic_probe.py            # defaults to Boeing (public)
    uv run topic_probe.py "Fluor"    # another public EPC vendor
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.rule import Rule
from tavily import TavilyClient

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
console = Console()


def show(resp, note: str = "") -> None:
    results = resp.get("results", [])
    if not results:
        console.print("  [red]— no results —[/red]")
        return
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        pub = r.get("published_date", "")  # present on topic=news
        pub_str = f" [magenta]({pub})[/magenta]" if pub else ""
        console.print(f"  [bold]{i}.[/bold] [green]{r.get('score', 0):.2f}[/green]{pub_str} {title}")
        console.print(f"     [dim]{url}[/dim]")


def main(vendor: str) -> None:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        console.print("[bold red]Missing TAVILY_API_KEY[/bold red]")
        raise SystemExit(1)
    client = TavilyClient(api_key=key)

    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()
    console.print(f"[cyan]Vendor:[/cyan] {vendor}   [dim](news window since {ninety_days_ago})[/dim]\n")

    # 1a. financial dimension via topic=finance
    console.print(Rule("[yellow]financial — topic=finance[/yellow]"))
    show(client.search(query=f"{vendor} backlog order book earnings financial results",
                       topic="finance", search_depth="advanced", max_results=5))

    # 1b. same query via topic=general (comparison)
    console.print(Rule("[yellow]financial — topic=general (compare)[/yellow]"))
    show(client.search(query=f"{vendor} backlog order book earnings financial results",
                       topic="general", search_depth="advanced", max_results=5))

    # 2. recent news via topic=news + 90-day start_date
    console.print(Rule("[yellow]news — topic=news, last 90 days[/yellow]"))
    show(client.search(query=f"{vendor} contract award project news",
                       topic="news", start_date=ninety_days_ago, max_results=8),
         note="check published_date is within ~90 days")

    # 3. country geo-boost on a general query
    console.print(Rule("[yellow]general + country=united states (geo boost)[/yellow]"))
    show(client.search(query=f"{vendor} company overview",
                       topic="general", country="united states", max_results=5))

    console.print(
        "\n[bold]Check:[/bold] does finance beat general for financial sourcing? "
        "are news results actually recent (published_date column)? did country change the mix?"
    )


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "Boeing")
