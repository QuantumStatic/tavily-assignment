from datetime import date

from vendor_dd.engine.history import ScoreHistory
from vendor_dd.engine.pipeline import Deps, ReportEngine
from vendor_dd.engine.schemas import Dimension, EntityCard, Section


class FakeSearch:
    def search(self, **kwargs):
        return {"results": [{"title": "t", "content": "c", "url": "https://x.com", "score": 0.8}]}


class FakeLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Acme", domain="acme.com", country="us", industry="steel")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=8)


def _deps(tmp_path):
    return Deps(search=FakeSearch(), llm=FakeLLM(), cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 11), fetch_transcript=lambda url: (None, None))


def test_fresh_generation_records_every_section_and_the_verdict(tmp_path):
    deps = _deps(tmp_path)
    history = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 7, 11))
    list(ReportEngine(deps, mode="sequential", history=history).iter_events("Acme"))

    scores = history.scores_for("acme.com")
    # all 6 scored dimensions + verdict recorded
    for dim in ("legal", "safety", "financial", "backlog", "certifications", "news"):
        assert dim in scores, f"{dim} not recorded"
    assert "verdict" in scores


def test_cached_sections_are_not_re_recorded(tmp_path):
    deps = _deps(tmp_path)
    # First run on day 1 seeds cache + history
    day1 = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 6, 1))
    list(ReportEngine(deps, mode="sequential", history=day1).iter_events("Acme"))

    # Second run on day 2: everything is cache-fresh, so nothing new should be recorded
    day2 = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 6, 2))
    list(ReportEngine(deps, mode="sequential", history=day2).iter_events("Acme"))

    # legal was served from cache on day 2 -> still only the day-1 row, no day-2 row
    assert day2.previous("acme.com", "legal") is None
    assert day2.scores_for("acme.com")["legal"][1] == "2026-06-01"


def test_all_cached_report_does_not_record_a_new_verdict(tmp_path):
    deps = _deps(tmp_path)
    # First run on day 1 seeds cache + history, including the verdict.
    day1 = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 6, 1))
    list(ReportEngine(deps, mode="sequential", history=day1).iter_events("Acme"))

    # Second run on day 2: every section + backlog is served from cache, so the
    # whole report is cache-fresh -> the verdict must NOT get a new day-2 row.
    day2 = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 6, 2))
    list(ReportEngine(deps, mode="sequential", history=day2).iter_events("Acme"))

    assert day2.previous("acme.com", "verdict") is None
    assert day2.scores_for("acme.com")["verdict"][1] == "2026-06-01"


def test_history_is_optional(tmp_path):
    # no history passed -> generation still works (CLI/unit path)
    deps = _deps(tmp_path)
    events = list(ReportEngine(deps, mode="sequential").iter_events("Acme"))
    assert any(type(e).__name__ == "ReportComplete" for e in events)
