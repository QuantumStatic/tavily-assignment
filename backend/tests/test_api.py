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
    assert names.count("section_complete") == 7   # 6 dims + backlog

    # vendor_key was backfilled during the stream, so the report is now readable
    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is True
    assert client.get(f"/projects/{pid}").json()["vendors"][0]["generated"] is True


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
    assert names.count("section_complete") == 6  # 7 minus the failed financial dim

    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is True
    assert len(report["sections"]) == 6
    assert "financial" not in {s["dimension"] for s in report["sections"]}
