from vendor_dd.engine.schemas import Dimension
from vendor_dd.surfaces.api.schemas import (
    ProjectIn, VendorIn, VendorOut, DimensionScore, VendorSummary, ProjectDetail, VendorReport,
)


def test_schemas_construct():
    assert ProjectIn(name="Bridge job").name == "Bridge job"
    assert VendorIn(name="Cives Steel").name == "Cives Steel"
    v = VendorOut(id=1, project_id=2, name="Cives", vendor_key=None, created_at="2026-07-09")
    assert v.vendor_key is None
    ds = DimensionScore(dimension=Dimension.LEGAL, score=8, as_of="2026-07-09T00:00:00+00:00")
    summ = VendorSummary(vendor_id=1, name="Cives", vendor_key="cives.com", generated=True,
                         verdict_score=6, verdict_reasoning="ok", dimensions=[ds])
    assert ProjectDetail(id=1, name="p", created_at="t", vendors=[summ]).vendors[0].generated
    assert VendorReport(generated=False, vendor_key=None, entity=None, verdict_score=None,
                        verdict_reasoning=None, sections=[]).generated is False


from datetime import date

from fastapi.testclient import TestClient

from vendor_dd.engine.schemas import EntityCard, Section, Dimension
from vendor_dd.engine.pipeline import Deps
from vendor_dd.surfaces.api.app import create_app


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


def _client(tmp_path):
    deps = Deps(search=FakeSearch(), llm=FakeLLM(), cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    return TestClient(create_app(deps))


def test_project_and_vendor_crud(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Bridge job"}).json()["id"]
    assert any(p["id"] == pid for p in client.get("/projects").json())

    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    detail = client.get(f"/projects/{pid}").json()
    assert detail["vendors"][0]["vendor_id"] == vid
    assert detail["vendors"][0]["generated"] is False   # no report yet

    client.delete(f"/vendors/{vid}")
    assert client.get(f"/projects/{pid}").json()["vendors"] == []


def test_report_read_model_empty_before_generation(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is False and report["sections"] == []


def test_missing_project_and_vendor_return_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/projects/9999").status_code == 404
    assert client.get("/vendors/9999/report").status_code == 404


def test_delete_missing_vendor_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.delete("/vendors/9999").status_code == 404


def _event_names(raw: str) -> list[str]:
    return [line[len("event:"):].strip()
            for line in raw.splitlines() if line.startswith("event:")]


def test_stream_endpoint_emits_full_event_sequence(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    names = _event_names(body)
    assert names[0] == "entity_resolved"
    assert names[-1] == "report_complete"
    assert names.count("section_complete") == 6   # 5 dims + backlog

    # vendor_key was backfilled during the stream, so the report is now readable
    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is True
    assert client.get(f"/projects/{pid}").json()["vendors"][0]["generated"] is True


def test_report_completes_and_caches_even_if_client_disconnects_early(tmp_path):
    """Generation is decoupled from the SSE stream: if the client stops listening
    (e.g. switches projects) the work keeps running and the report still ends up fully
    cached — coming back shows it complete, not stuck partial."""
    import time
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    # open the stream but bail out after the first line (simulates a disconnect)
    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        for _ in resp.iter_lines():
            break

    # the background thread keeps going; poll until the report is fully generated
    report = {}
    for _ in range(100):
        report = client.get(f"/vendors/{vid}/report").json()
        if report.get("generated"):
            break
        time.sleep(0.02)
    assert report["generated"] is True
    assert len(report["sections"]) == 6           # all sections cached despite the disconnect


def test_stream_missing_vendor_returns_404(tmp_path):
    client = _client(tmp_path)
    resp = client.get("/vendors/9999/report/stream")
    assert resp.status_code == 404


class OneDimFailsLLM:
    """Like FakeLLM but raises for the FINANCIAL synthesis prompt."""

    def structured(self, prompt, schema):
        if schema is EntityCard:
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        if "financial" in prompt:
            raise RuntimeError("boom")
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="ok", score=7)


def test_stream_emits_section_error_and_still_completes(tmp_path):
    deps = Deps(search=FakeSearch(), llm=OneDimFailsLLM(), cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    client = TestClient(create_app(deps))
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert "event: section_error" in body       # the failing dim surfaced as an SSE frame
    names = _event_names(body)
    assert names.count("section_error") == 1
    assert names[-1] == "report_complete"       # stream still ends normally
    assert names.count("section_complete") == 5  # 6 minus the failed financial dim

    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is True
    assert len(report["sections"]) == 5
    assert "financial" not in {s["dimension"] for s in report["sections"]}


def test_stream_uses_project_session_id(tmp_path):
    # A recording search lets us assert the project's session_id reached Tavily.
    class RecordingSearch:
        def __init__(self):
            self.calls = []
        def search(self, **kwargs):
            self.calls.append(kwargs)
            return {"results": [{"title": "x", "content": "Cives Steel Company",
                                 "url": "https://x.com", "score": 0.8}]}

    search = RecordingSearch()
    deps = Deps(search=search, llm=FakeLLM(), cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    app = create_app(deps)
    client = TestClient(app)

    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        "".join(resp.iter_text())

    sid = app.state.store.get_project(pid).session_id
    assert sid and search.calls
    assert all(c.get("session_id") == sid for c in search.calls)


def test_http_middleware_logs_request_and_response(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    client = _client(tmp_path)
    client.post("/projects", json={"name": "p"})
    lines = [json.loads(l) for l in (tmp_path / "logs" / "http.log").read_text().splitlines() if l.strip()]
    events = [o["event"] for o in lines]
    assert "http.request" in events and "http.response" in events
    resp = next(o for o in lines if o["event"] == "http.response")
    assert resp["payload"]["status"] == 200 and "latency_ms" in resp["payload"]


def test_http_middleware_does_not_buffer_the_sse_stream(tmp_path):
    import json
    from vendor_dd.logs import configure_logging
    configure_logging(tmp_path / "logs", level="INFO")
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        "".join(resp.iter_text())
    lines = [json.loads(l) for l in (tmp_path / "logs" / "http.log").read_text().splitlines() if l.strip()]
    stream_resp = [o for o in lines if o["event"] == "http.response" and "/report/stream" in o["payload"]["path"]]
    assert stream_resp and stream_resp[-1]["payload"].get("body") in (None, "<streaming>")


def test_read_model_reports_section_completeness(tmp_path):
    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension, Section
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    # backfill vendor_key + seed just 2 of 6 sections directly into the shared cache
    key = "cives.com"
    client.app.state.store.set_vendor_key(vid, key)
    cache = SQLiteCache(tmp_path / "db.sqlite")
    for dim in (Dimension.LEGAL, Dimension.FINANCIAL):
        cache.put(key, dim, Section(dimension=dim, findings=[], reasoning="x", score=6).model_dump(mode="json"))
    cache.close()

    summ = client.get(f"/projects/{pid}").json()["vendors"][0]
    assert summ["sections_present"] == 2 and summ["sections_expected"] == 6
    report = client.get(f"/vendors/{vid}/report").json()
    assert report["sections_present"] == 2 and report["sections_expected"] == 6
