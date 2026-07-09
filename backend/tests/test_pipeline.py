from datetime import date
from vendor_dd.engine.schemas import Dimension, EntityCard, Section, Report
from vendor_dd.engine.pipeline import run_report, Deps


class FakeSearch:
    def search(self, **kwargs):
        return {"results": [{"title": "Cives Steel news", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


class FakeLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


def test_run_report_returns_report_with_all_dimensions(tmp_path):
    deps = Deps(search=FakeSearch(), llm=FakeLLM(), cache_path=tmp_path / "c.db",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    report = run_report("Cives Steel", deps)
    assert isinstance(report, Report)
    assert report.entity.domain == "cives.com"
    # one section per non-snapshot dimension
    assert {s.dimension for s in report.sections} == {
        d for d in Dimension if d is not Dimension.SNAPSHOT}
    assert 0 <= report.verdict_score <= 10
