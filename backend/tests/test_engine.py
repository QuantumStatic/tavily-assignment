from vendor_dd.engine.schemas import Dimension, EntityCard, Section, Report
from vendor_dd.engine.events import (
    EntityResolved, SectionComplete, SectionError, ReportComplete, ReportError,
)
from vendor_dd.engine.llm import LLMError


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
    # 5 Tavily dims + backlog = 6 sections
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
    assert events[0].message == "report could not be generated"


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
    assert len(events) == 7
    completed = [e for e in events[1:6] if e.type == "section_complete"]
    assert len(completed) == 5
    dims = {e.section.dimension for e in completed}
    assert dims == {d for d in Dimension if d not in (Dimension.SNAPSHOT, Dimension.BACKLOG)}
    assert isinstance(events[-1], ReportError)
    assert events[-1].message == "report could not be generated"
    assert not any(e.type == "report_complete" for e in events)


class BacklogLLMErrorOnceLLM:
    """Raises LLMError on the first backlog synthesis call, succeeds on retry."""

    def __init__(self):
        self.backlog_calls = 0

    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        if "backlog" in prompt:
            self.backlog_calls += 1
            if self.backlog_calls == 1:
                raise LLMError("backlog transient failure")
            return Section(dimension=Dimension.BACKLOG, findings=[], reasoning="ok", score=7)
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def test_backlog_llm_error_is_retried_once_and_recovers(tmp_path):
    llm = BacklogLLMErrorOnceLLM()
    engine = ReportEngine(_deps(tmp_path, llm=llm), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    assert events[-1].type == "report_complete"
    assert llm.backlog_calls == 2
    completed_dims = {e.section.dimension for e in events if e.type == "section_complete"}
    assert Dimension.BACKLOG in completed_dims


class BacklogLLMErrorAlwaysLLM:
    """Raises LLMError on every backlog synthesis call (retry exhausted)."""

    def __init__(self):
        self.backlog_calls = 0

    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        if "backlog" in prompt:
            self.backlog_calls += 1
            raise LLMError("backlog persistent failure")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def test_backlog_llm_error_after_retry_still_yields_report_error(tmp_path):
    llm = BacklogLLMErrorAlwaysLLM()
    engine = ReportEngine(_deps(tmp_path, llm=llm), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    assert isinstance(events[-1], ReportError)
    assert events[-1].message == "report could not be generated"
    assert llm.backlog_calls == 2
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


class RecordingSearch:
    """Like StatelessSearch but records every search call's kwargs."""

    def __init__(self):
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {"results": [{"title": "Cives Steel news", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


class PublicEntityRecordingLLM:
    """Resolves a public entity (NYSE:BA) and records every prompt it receives."""

    def __init__(self):
        self.prompts = []

    def structured(self, prompt, schema):
        self.prompts.append(prompt)
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=True, ticker="BA", exchange="NYSE")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def test_public_company_backlog_uses_transcript_not_search(tmp_path):
    transcript = "transcript text " * 1000          # 16000 chars, > 6000 truncation limit
    assert len(transcript) == 16000
    search = RecordingSearch()
    llm = PublicEntityRecordingLLM()
    deps = Deps(search=search, llm=llm, cache_path=tmp_path / "c.db", today=date(2026, 7, 8),
                fetch_transcript=lambda url: (transcript, "2026-04-16"))
    events = list(ReportEngine(deps, mode="sequential").iter_events("Cives Steel"))

    # backlog section completed successfully and the report finished
    assert events[-1].type == "report_complete"
    completed_dims = {e.section.dimension for e in events if e.type == "section_complete"}
    assert Dimension.BACKLOG in completed_dims

    # transcript path used, NOT the news-reuse/search fallback: exactly one search for
    # entity resolution plus one per Tavily dimension -- no extra backlog/news re-fetch
    assert len(search.calls) == 1 + len(
        [d for d in Dimension if d not in (Dimension.SNAPSHOT, Dimension.BACKLOG)])

    # the backlog synthesis prompt embeds the transcript truncated to 6000 chars
    backlog_prompts = [p for p in llm.prompts if "backlog" in p]
    assert len(backlog_prompts) == 1
    prompt = backlog_prompts[0]
    assert "Earnings call transcript" in prompt
    assert transcript[:6000] in prompt        # first 6000 chars present...
    assert transcript not in prompt           # ...but the full 16000-char text is not


def test_session_id_injected_into_every_search(tmp_path):
    search = RecordingSearch()
    deps = Deps(search=search, llm=StatelessLLM(), cache_path=tmp_path / "c.db",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    ReportEngine(deps, mode="sequential", session_id="sess-xyz").run_report("Cives Steel")
    assert search.calls, "expected search to be called"
    assert all(c.get("session_id") == "sess-xyz" for c in search.calls)
    assert all(c.get("client_name") == "vendor-dd" for c in search.calls)


def test_no_session_id_leaves_search_kwargs_untouched(tmp_path):
    search = RecordingSearch()
    deps = Deps(search=search, llm=StatelessLLM(), cache_path=tmp_path / "c.db",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    ReportEngine(deps, mode="sequential").run_report("Cives Steel")   # no session_id
    assert search.calls
    assert all("session_id" not in c for c in search.calls)


def test_section_error_message_is_generic_but_logged(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    engine = ReportEngine(_deps(tmp_path, llm=OneDimFailsLLM()), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))
    err = next(e for e in events if e.type == "section_error")
    assert "boom" not in err.message                      # raw exception text not surfaced
    assert err.dimension is Dimension.FINANCIAL
    lines = (tmp_path / "logs" / "general.log").read_text()
    assert "boom" in lines                                # ...but the detail IS logged server-side


def test_verdict_failure_yields_generic_report_error_and_is_logged(tmp_path, monkeypatch):
    from vendor_dd.logs import configure_logging
    from vendor_dd.engine.events import ReportError
    import vendor_dd.engine.pipeline as pipeline_mod

    configure_logging(tmp_path / "logs", level="INFO")

    def _boom(sections):
        raise RuntimeError("verdict boom")

    monkeypatch.setattr(pipeline_mod, "assemble_verdict", _boom)

    engine = ReportEngine(_deps(tmp_path), mode="sequential")
    events = list(engine.iter_events("Cives Steel"))

    assert isinstance(events[-1], ReportError)
    assert events[-1].message == "report could not be generated"
    assert "verdict boom" not in events[-1].message         # raw exception text not surfaced
    assert not any(e.type == "report_complete" for e in events)

    lines = (tmp_path / "logs" / "general.log").read_text()
    assert "verdict boom" in lines                          # ...but the detail IS logged server-side
