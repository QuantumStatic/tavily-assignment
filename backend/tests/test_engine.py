from vendor_dd.engine.schemas import Dimension, EntityCard, Section, Report
from vendor_dd.engine.events import (
    EntityResolved, SectionComplete, SectionError, ReportComplete, ReportError,
)


def _entity():
    return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                      industry="steel", is_public=False)


def _section():
    return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


def test_event_types_carry_discriminator_and_payload():
    assert EntityResolved(entity=_entity()).type == "entity_resolved"
    sc = SectionComplete(section=_section(), cached=True)
    assert sc.type == "section_complete" and sc.cached is True
    se = SectionError(dimension=Dimension.FINANCIAL, message="boom")
    assert se.type == "section_error" and se.dimension is Dimension.FINANCIAL
    rc = ReportComplete(report=Report(vendor_input="x", entity=_entity(), sections=[],
                                      verdict_score=5, verdict_reasoning="r"))
    assert rc.type == "report_complete"
    assert ReportError(message="fatal").type == "report_error"


def test_section_complete_defaults_cached_false():
    assert SectionComplete(section=_section()).cached is False


from datetime import date
from vendor_dd.engine.pipeline import ReportEngine, Deps, run_report


class StatelessSearch:
    def search(self, **kwargs):
        return {"results": [{"title": "Cives Steel news", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


class StatelessLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="clean", score=8)


class OneDimFailsLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        if "financial" in prompt:
            raise RuntimeError("boom")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def _deps(tmp_path, llm=None):
    return Deps(search=StatelessSearch(), llm=llm or StatelessLLM(),
                cache_path=tmp_path / "c.db", today=date(2026, 7, 8),
                fetch_transcript=lambda url: (None, None))


def test_iter_events_sequence(tmp_path):
    engine = ReportEngine(_deps(tmp_path), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    assert events[0].type == "entity_resolved"
    assert events[-1].type == "report_complete"
    completed = [e for e in events if e.type == "section_complete"]
    # 6 Tavily dims + backlog = 7 sections
    dims = {e.section.dimension for e in completed}
    assert dims == {d for d in Dimension if d is not Dimension.SNAPSHOT}


def test_cache_hit_streams_cached_true(tmp_path):
    deps = _deps(tmp_path)
    engine = ReportEngine(deps, mode="sequential")
    list(engine.iter_events("Cives Steel"))            # populate cache
    events = list(engine.iter_events("Cives Steel"))   # second run: all cached
    completed = [e for e in events if e.type == "section_complete"]
    assert completed and all(e.cached for e in completed)


def test_section_error_does_not_abort_report(tmp_path):
    engine = ReportEngine(_deps(tmp_path, llm=OneDimFailsLLM()), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    errors = [e for e in events if e.type == "section_error"]
    assert [e.dimension for e in errors] == [Dimension.FINANCIAL]
    assert events[-1].type == "report_complete"          # report still completes
    dims = {e.section.dimension for e in events if e.type == "section_complete"}
    assert Dimension.FINANCIAL not in dims               # failed dim absent


def test_sequential_and_parallel_produce_same_report(tmp_path):
    seq = ReportEngine(_deps(tmp_path / "seq"), mode="sequential").run_report("Cives Steel")
    par = ReportEngine(_deps(tmp_path / "par"), mode="parallel").run_report("Cives Steel")
    assert seq.verdict_score == par.verdict_score
    assert {(s.dimension, s.score) for s in seq.sections} == \
           {(s.dimension, s.score) for s in par.sections}


def test_run_report_shim_still_returns_report(tmp_path):
    report = run_report("Cives Steel", _deps(tmp_path))
    assert isinstance(report, Report)
    assert report.entity.domain == "cives.com"


class EntityResolutionFailsLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            raise RuntimeError("entity resolution boom")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def test_entity_resolution_failure_yields_report_error_and_stops(tmp_path):
    from vendor_dd.engine.events import ReportError
    engine = ReportEngine(_deps(tmp_path, llm=EntityResolutionFailsLLM()), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    assert len(events) == 1
    assert isinstance(events[0], ReportError)
    assert "entity resolution boom" in events[0].message


class BacklogFailsLLM:
    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        if "backlog" in prompt:
            raise RuntimeError("backlog boom")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def test_backlog_failure_yields_report_error(tmp_path):
    from vendor_dd.engine.events import ReportError
    engine = ReportEngine(_deps(tmp_path, llm=BacklogFailsLLM()), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    assert events[0].type == "entity_resolved"
    assert len(events) == 8
    completed = [e for e in events[1:7] if e.type == "section_complete"]
    assert len(completed) == 6
    dims = {e.section.dimension for e in completed}
    assert dims == {d for d in Dimension if d not in (Dimension.SNAPSHOT, Dimension.BACKLOG)}
    assert isinstance(events[-1], ReportError)
    assert "backlog boom" in events[-1].message
    assert not any(e.type == "report_complete" for e in events)


def test_iter_events_closes_cache_when_generator_abandoned(tmp_path):
    import sqlite3
    engine = ReportEngine(_deps(tmp_path), mode="sequential")
    gen = engine.iter_events("Cives Steel")
    next(gen)  # consume just the first event (EntityResolved), then abandon
    gen.close()  # explicitly trigger GeneratorExit, simulating client-disconnect cleanup
    # the cache this generator opened should now be closed; a fresh cache against
    # the same path should still work fine (proves no corruption / file-lock left behind)
    from vendor_dd.engine.cache import SQLiteCache
    fresh = SQLiteCache(tmp_path / "c.db")
    assert fresh.get("cives steel", Dimension.SNAPSHOT) is not None  # entity was cached before abandonment
    fresh.close()
