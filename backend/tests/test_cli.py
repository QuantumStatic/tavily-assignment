"""Smoke tests for the `vendor-dd` CLI — the one surface that only adapts the engine
to the terminal. The engine itself is covered elsewhere; here we drive `main` with a
fake engine (no network) and assert the event→console rendering and the exit codes."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

import vendor_dd.surfaces.cli as cli
from vendor_dd.engine.events import EntityResolved, ReportComplete, SectionComplete
from vendor_dd.engine.schemas import Dimension, EntityCard, Report, Section
from vendor_dd.logs import correlation_id_var

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_correlation_id():
    """main() sets a per-run correlation id on a module-level contextvar; restore the
    default afterward so it doesn't leak into other tests' log-record assertions."""
    yield
    correlation_id_var.set(None)


class _FakeEngine:
    """Stands in for ReportEngine: yields a scripted, network-free event stream."""

    def __init__(self, *args, **kwargs):
        pass

    def iter_events(self, vendor: str):
        entity = EntityCard(name=vendor, domain="example.com", industry="engineering",
                            country="United States", is_public=False)
        section = Section(dimension=Dimension.LEGAL, findings=[], reasoning="no issues found", score=6)
        yield EntityResolved(entity=entity)
        yield SectionComplete(section=section, cached=False)
        yield ReportComplete(report=Report(vendor_input=vendor, entity=entity, sections=[section],
                                           verdict_score=6, verdict_reasoning="Looks acceptable."))


def _patch_engine(monkeypatch):
    monkeypatch.setattr(cli, "ReportEngine", _FakeEngine)
    monkeypatch.setattr(cli, "TavilySearchClient", lambda *a, **k: object())
    monkeypatch.setattr(cli, "OpenAILLM", lambda *a, **k: object())


def test_cli_renders_a_streamed_report(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # keep the cache db + logs out of the repo
    monkeypatch.setenv("TAVILY_API_KEY", "t")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    _patch_engine(monkeypatch)

    result = runner.invoke(cli.app, ["Acme Corp"])

    assert result.exit_code == 0
    assert "Acme Corp" in result.stdout        # entity panel
    assert "6/10" in result.stdout             # verdict + section score rendered


def test_cli_exits_when_keys_are_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_engine(monkeypatch)

    result = runner.invoke(cli.app, ["Acme Corp"])

    assert result.exit_code == 1
    assert "TAVILY_API_KEY" in result.stdout
