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


class CountingSearch:
    """Like FakeSearch but tracks how many times search() was invoked."""

    def __init__(self):
        self.call_count = 0

    def search(self, **kwargs):
        self.call_count += 1
        return {"results": [{"title": "Cives Steel news", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


class CountingLLM:
    """Like FakeLLM but tracks how many times structured() was invoked for EntityCard."""

    def __init__(self):
        self.entity_call_count = 0

    def structured(self, prompt, schema):
        if schema is EntityCard:
            self.entity_call_count += 1
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


def test_entity_resolution_is_cached_across_run_report_calls(tmp_path):
    search = CountingSearch()
    llm = CountingLLM()
    cache_path = tmp_path / "c.db"
    deps = Deps(search=search, llm=llm, cache_path=cache_path,
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))

    run_report("Cives Steel", deps)
    entity_calls_after_first = llm.entity_call_count
    search_calls_after_first = search.call_count
    assert entity_calls_after_first == 1  # resolve_entity ran once (cache miss)

    run_report("Cives Steel", deps)
    # entity resolution must not run a second time: no new EntityCard LLM call
    assert llm.entity_call_count == entity_calls_after_first
    # and no new search call attributable to entity resolution either
    # (all dimension/backlog sections are also cached by the second run, so total
    # search calls should not increase at all)
    assert search.call_count == search_calls_after_first


def test_backlog_reuses_news_positive_results_for_private_vendor(tmp_path):
    search = CountingSearch()
    llm = CountingLLM()
    cache_path = tmp_path / "c.db"
    deps = Deps(search=search, llm=llm, cache_path=cache_path,
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))

    run_report("Cives Steel", deps)

    # one search call for entity resolution + one per Tavily-backed dimension
    # (NEWS_POSITIVE's results are reused for BACKLOG instead of triggering a second
    # identical search call).
    expected_calls = 1 + len(
        [d for d in Dimension if d not in (Dimension.SNAPSHOT, Dimension.BACKLOG)]
    )
    assert search.call_count == expected_calls
