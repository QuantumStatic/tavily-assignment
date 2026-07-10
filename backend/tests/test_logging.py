import json
import logging

from vendor_dd.logs import (
    configure_logging, get_logger, correlation_id_var, set_correlation_id,
)
from vendor_dd.logs.formatter import JsonLineFormatter, redact


def _read_lines(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_formatter_emits_one_json_object_per_line():
    rec = logging.LogRecord("vendor_dd.llm", logging.INFO, __file__, 1,
                            "llm.request", None, None)
    rec.payload = {"model": "m", "prompt": "hi"}
    rec.correlation_id = "abc"
    line = JsonLineFormatter().format(rec)
    obj = json.loads(line)
    assert obj["logger"] == "vendor_dd.llm"
    assert obj["event"] == "llm.request"
    assert obj["correlation_id"] == "abc"
    assert obj["payload"] == {"model": "m", "prompt": "hi"}
    assert obj["level"] == "INFO" and "ts" in obj


def test_redact_scrubs_known_secret_values(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "super-secret-123")
    assert redact({"api_key": "super-secret-123", "q": "hello"}) == {"api_key": "***", "q": "hello"}
    assert redact("prefix super-secret-123 suffix") == "prefix *** suffix"


def test_configure_logging_creates_five_files_and_isolates(tmp_path):
    configure_logging(tmp_path, level="INFO")
    get_logger("db").info("db.query", extra={"payload": {"sql": "SELECT 1"}})
    for name in ("general", "llm", "tavily", "http", "db"):
        assert (tmp_path / f"{name}.log").exists()
    db_lines = _read_lines(tmp_path / "db.log")
    assert any(o["event"] == "db.query" for o in db_lines)
    # isolation: the db record did NOT leak into general.log
    assert _read_lines(tmp_path / "general.log") == []


def test_correlation_id_flows_onto_records(tmp_path):
    configure_logging(tmp_path, level="INFO")
    token = set_correlation_id("corr-42")
    try:
        get_logger("general").info("app.startup", extra={"payload": {}})
    finally:
        correlation_id_var.reset(token)
    lines = _read_lines(tmp_path / "general.log")
    assert lines and lines[-1]["correlation_id"] == "corr-42"


def test_missing_correlation_id_is_null(tmp_path):
    configure_logging(tmp_path, level="INFO")
    get_logger("general").info("x", extra={"payload": {}})
    assert _read_lines(tmp_path / "general.log")[-1]["correlation_id"] is None


def test_correlation_id_propagates_into_engine_worker_threads(tmp_path):
    import json
    from datetime import date
    from vendor_dd.engine.pipeline import ReportEngine, Deps
    from vendor_dd.engine.schemas import EntityCard, Section, Dimension
    configure_logging(tmp_path / "logs", level="INFO")

    class Search:
        def search(self, **kwargs):
            return {"results": [{"title": "x", "content": "Cives Steel", "url": "u", "score": 0.8}]}

    class LLM:
        def structured(self, prompt, schema):
            if schema is EntityCard:
                return EntityCard(name="Cives Steel", domain="cives.com", is_public=False)
            return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=8)

    deps = Deps(search=Search(), llm=LLM(), cache_path=tmp_path / "c.db",
                today=date(2026, 7, 8), fetch_transcript=lambda u: (None, None))
    token = set_correlation_id("run-99")
    try:
        ReportEngine(deps, mode="parallel").run_report("Cives Steel")
    finally:
        correlation_id_var.reset(token)

    # section.computed is emitted from a worker thread; it must still carry the id,
    # proving contextvars.copy_context() propagation into the ThreadPoolExecutor worker.
    lines = [json.loads(l) for l in (tmp_path / "logs" / "general.log").read_text().splitlines() if l.strip()]
    section_events = [o for o in lines if o["event"] == "section.computed"]
    assert section_events and all(o["correlation_id"] == "run-99" for o in section_events)
