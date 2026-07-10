from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vendor_dd.engine.backlog import fetch_transcript_text
from vendor_dd.engine.events import (
    EntityResolved, ReportComplete, SectionComplete, SectionError,
)
from vendor_dd.engine.llm import NebiusLLM
from vendor_dd.engine.pipeline import Deps, ReportEngine
from vendor_dd.engine.tavily_client import TavilySearchClient

# .env lives at the project root (one level above backend/): cli.py is
# backend/src/vendor_dd/surfaces/cli.py, so parents[4] == project root.
# (This corrects a latent Phase 1 path that pointed at backend/.env.)
load_dotenv(Path(__file__).resolve().parents[4] / ".env")
app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def main(vendor: str) -> None:
    """Generate a due-diligence report for VENDOR (streams sections as they complete)."""
    tavily_key, nebius_key = os.getenv("TAVILY_API_KEY"), os.getenv("NEBIUS_API_KEY")
    if not tavily_key or not nebius_key:
        console.print("[red]Set TAVILY_API_KEY and NEBIUS_API_KEY in .env[/red]")
        raise typer.Exit(1)

    deps = Deps(
        search=TavilySearchClient(tavily_key),
        llm=NebiusLLM(),
        cache_path=Path(".vendor_dd_cache.db"),
        today=date.today(),
        fetch_transcript=fetch_transcript_text,
    )
    engine = ReportEngine(deps, mode="parallel")

    report = None
    with console.status(f"Researching {vendor}..."):
        for ev in engine.iter_events(vendor):
            if isinstance(ev, EntityResolved):
                e = ev.entity
                console.print(Panel.fit(
                    f"[bold]{e.name}[/bold]  ·  {e.industry or '?'}  ·  {e.country or '?'}  "
                    f"·  {'public ' + (e.ticker or '') if e.is_public else 'private'}",
                    title="Entity", border_style="cyan"))
            elif isinstance(ev, SectionComplete):
                s = ev.section
                tag = " [dim](cached)[/dim]" if ev.cached else ""
                console.print(f"  ✓ {s.dimension.value}: {s.score}/10{tag}")
            elif isinstance(ev, SectionError):
                console.print(f"  [red]✗ {ev.dimension.value} failed: {ev.message}[/red]")
            elif isinstance(ev, ReportComplete):
                report = ev.report

    if report is None:
        console.print("[red]Report did not complete.[/red]")
        raise typer.Exit(1)

    console.print(Panel.fit(report.verdict_reasoning,
                            title=f"Verdict {report.verdict_score}/10", border_style="cyan"))

    table = Table("Dimension", "Score", "Reasoning")
    for s in sorted(report.sections, key=lambda s: s.score):  # riskiest first
        color = "green" if s.score >= 7 else "yellow" if s.score >= 4 else "red"
        table.add_row(s.dimension.value, f"[{color}]{s.score}/10[/{color}]", s.reasoning[:80])
    console.print(table)

    for s in report.sections:
        for f in s.findings:
            c = f.citation
            console.print(f"  [{s.dimension.value}] {f.claim}")
            console.print(f"     [dim]{c.url} · {c.source_type.value} · as of {c.as_of or 'n/a'}[/dim]")


if __name__ == "__main__":
    app()
