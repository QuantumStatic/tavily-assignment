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


def test_rename_vendor_route(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Stel"}).json()["id"]
    client.post(f"/projects/{pid}/vendors", json={"name": "Fluor"})

    ok = client.patch(f"/vendors/{vid}", json={"name": "  Cives Steel "})
    assert ok.status_code == 200 and ok.json()["name"] == "Cives Steel"

    assert client.patch(f"/vendors/{vid}", json={"name": "   "}).status_code == 422
    assert client.patch(f"/vendors/{vid}", json={"name": "fluor"}).status_code == 409   # collision
    assert client.patch("/vendors/9999", json={"name": "x"}).status_code == 404


def test_missing_project_and_vendor_return_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/projects/9999").status_code == 404
    assert client.get("/vendors/9999/report").status_code == 404


def test_delete_missing_vendor_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.delete("/vendors/9999").status_code == 404


def test_adding_a_duplicate_vendor_returns_the_existing_row_no_second_insert(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    first = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()
    assert first["existed"] is False

    dup = client.post(f"/projects/{pid}/vendors", json={"name": "  cives steel "})  # case/space variant
    assert dup.status_code == 200
    body = dup.json()
    assert body["existed"] is True
    assert body["id"] == first["id"]                 # same row, not a new one
    # still exactly one row
    assert len(client.get(f"/projects/{pid}").json()["vendors"]) == 1

    # the same name in a DIFFERENT project is allowed (a genuinely new row there)
    pid2 = client.post("/projects", json={"name": "p2"}).json()["id"]
    other = client.post(f"/projects/{pid2}/vendors", json={"name": "Cives Steel"}).json()
    assert other["existed"] is False
    assert other["id"] != first["id"]


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


def test_report_shows_the_entity_even_when_the_snapshot_is_past_its_ttl(tmp_path):
    """The sections shown in a report are read TTL-free (all_sections), so the entity
    header must be too. Otherwise a report untouched past the 30-day snapshot TTL would
    render its scores with a blank company header (entity=null)."""
    from datetime import datetime, timezone

    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension, Section

    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]
    key = "cives.com"
    client.app.state.store.set_vendor_key(vid, key)

    # seed a section + entity snapshot stamped far in the past (older than every TTL)
    stale = SQLiteCache(tmp_path / "db.sqlite",
                        clock=lambda: datetime(2020, 1, 1, tzinfo=timezone.utc))
    stale.put(key, Dimension.LEGAL,
              Section(dimension=Dimension.LEGAL, findings=[], reasoning="x", score=6).model_dump(mode="json"))
    stale.put(key, Dimension.SNAPSHOT,
              EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                         industry="steel", is_public=False).model_dump(mode="json"))
    stale.close()

    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is True
    assert report["entity"] is not None, "entity header blanked out by the snapshot TTL"
    assert report["entity"]["domain"] == "cives.com"


def test_add_vendor_lost_race_falls_back_to_the_existing_row(tmp_path, monkeypatch):
    """If the pre-insert duplicate check misses (concurrent add), the DB constraint
    rejects the insert and the route returns the winner's row instead of a 500."""
    from vendor_dd.surfaces.api.store import Store

    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    first = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()

    real = Store.find_vendor
    calls = {"n": 0}

    def racy(self, project_id, name):
        calls["n"] += 1
        if calls["n"] == 1:
            return None   # simulate the check running before the concurrent insert landed
        return real(self, project_id, name)

    monkeypatch.setattr(Store, "find_vendor", racy)
    dup = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"})
    assert dup.status_code == 200
    assert dup.json()["existed"] is True
    assert dup.json()["id"] == first["id"]


import threading


class GatedLLM:
    """Entity resolution is instant; section synthesis blocks on a gate the test
    controls, so generation stays in flight for as long as the test needs."""

    def __init__(self):
        self.gate = threading.Event()
        self.entity_calls = 0
        self.section_calls = 0

    def structured(self, prompt, schema):
        if schema is EntityCard:
            self.entity_calls += 1
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        self.section_calls += 1
        assert self.gate.wait(timeout=10), "test never opened the gate"
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="x", score=5)


def test_second_stream_for_the_same_vendor_tails_the_existing_run(tmp_path):
    """Two concurrent streams must NOT start two generations (double Tavily/LLM
    spend, racing cache writes). The second subscriber replays history and tails.

    NOTE: this FastAPI TestClient/httpx version buffers the entire SSE body before
    iter_lines()/iter_text() yields anything, so a nested `with client.stream(...)`
    never actually observes the first stream mid-flight — it just serializes the
    two requests. We fall back to running each stream on its own thread and use
    vendor_key (backfilled synchronously the moment EntityResolved fires, well
    before section synthesis is even submitted) as the "generation is now blocked
    on the gate" signal instead of a partial read of the SSE body."""
    import time

    llm = GatedLLM()
    deps = Deps(search=FakeSearch(), llm=llm, cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    app = create_app(deps)
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    result1: dict = {}
    result2: dict = {}

    def run_first():
        with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
            result1["body"] = "".join(resp.iter_text())

    t1 = threading.Thread(target=run_first)
    t1.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and app.state.store.get_vendor(vid).vendor_key is None:
        time.sleep(0.02)
    assert app.state.store.get_vendor(vid).vendor_key is not None, "entity never resolved"

    def run_second():
        with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
            result2["body"] = "".join(resp.iter_text())

    t2 = threading.Thread(target=run_second)
    t2.start()
    time.sleep(0.2)   # give the second request a moment to subscribe before unblocking sections
    llm.gate.set()    # let sections finish; both streams should now drain

    t1.join(timeout=15)
    t2.join(timeout=15)
    assert not t1.is_alive() and not t2.is_alive()

    assert llm.entity_calls == 1                       # one generation, not two
    # entity_calls alone doesn't prove single-flight: entity resolution is independently
    # cache-guarded in _resolve_entity_cached, so it stays at 1 even if a second, fully
    # independent generation ran. Section synthesis is NOT cache-guarded across concurrent
    # runs the same way, so it's the real signal for double spend. One generation computes
    # exactly one section per dimension: the five Tavily dims (LEGAL, SAFETY, FINANCIAL,
    # CERTIFICATIONS, NEWS) plus BACKLOG, all uncached on a brand-new vendor -> 6 calls.
    # If a second generation ran independently (the pre-fix bug), this would be ~12.
    assert llm.section_calls == 6                      # one generation's worth, not two
    names2 = _event_names(result2["body"])
    assert names2[0] == "entity_resolved"              # history was replayed
    assert names2[-1] == "report_complete"
    assert _event_names(result1["body"])[-1] == "report_complete"


def test_delete_mid_generation_evicts_the_cache_the_zombie_run_writes(tmp_path):
    """Deleting a vendor evicts its cache — but generation keeps running and used
    to re-write sections AFTER that eviction, resurrecting a report for a vendor
    that no longer exists (and poisoning a later re-add). The generation thread
    must clean up after itself when its vendor is gone."""
    import time

    from vendor_dd.engine.cache import SQLiteCache

    llm = GatedLLM()
    deps = Deps(search=FakeSearch(), llm=llm, cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    app = create_app(deps)
    client = TestClient(app)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    # Same TestClient buffering issue as the single-flight test above: run the stream
    # on its own thread and use vendor_key (backfilled the instant EntityResolved
    # fires) as the "generation is now blocked on the gate" signal.
    t = threading.Thread(
        target=lambda: client.get(f"/vendors/{vid}/report/stream").text)
    t.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and app.state.store.get_vendor(vid).vendor_key is None:
        time.sleep(0.02)
    assert app.state.store.get_vendor(vid).vendor_key is not None, "entity never resolved"

    client.delete(f"/vendors/{vid}")   # runs its own eviction; the run is still gated
    llm.gate.set()                     # sections now synthesize and hit the cache
    t.join(timeout=15)
    assert not t.is_alive()

    # generation drains, notices the vendor is gone, and evicts what it wrote
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        cache = SQLiteCache(tmp_path / "db.sqlite")
        sections = cache.all_sections("cives.com")
        cache.close()
        if not sections:
            break
        time.sleep(0.05)
    assert sections == {}, "zombie generation resurrected evicted cache entries"


def test_blank_names_are_rejected_and_stored_names_are_trimmed(tmp_path):
    client = _client(tmp_path)
    assert client.post("/projects", json={"name": "   "}).status_code == 422
    pid = client.post("/projects", json={"name": "  Bridge job  "}).json()["id"]
    assert client.get(f"/projects/{pid}").json()["name"] == "Bridge job"

    assert client.post(f"/projects/{pid}/vendors", json={"name": " \t "}).status_code == 422
    v = client.post(f"/projects/{pid}/vendors", json={"name": "  Cives Steel  "}).json()
    assert v["name"] == "Cives Steel"


def test_delete_project_removes_it_and_its_vendors(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    assert client.delete(f"/projects/{pid}").status_code == 200
    assert client.get(f"/projects/{pid}").status_code == 404
    assert client.get(f"/vendors/{vid}/report").status_code == 404
    assert client.delete("/projects/9999").status_code == 404


def test_project_detail_flags_domain_level_duplicates(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    v1 = client.post(f"/projects/{pid}/vendors", json={"name": "Voith"}).json()["id"]
    v2 = client.post(f"/projects/{pid}/vendors", json={"name": "Voith Hydro"}).json()["id"]
    store = client.app.state.store
    store.set_vendor_key(v1, "voith.com")
    store.set_vendor_key(v2, "voith.com")

    vendors = client.get(f"/projects/{pid}").json()["vendors"]
    assert vendors[0]["duplicate_of"] is None
    assert vendors[1]["duplicate_of"] == "Voith"   # points at the earlier (canonical) row


def test_rename_mid_generation_does_not_orphan_the_entity_snapshot(tmp_path):
    """Fix 2: the entity snapshot is keyed by DOMAIN (stable across renames), not by the
    vendor's name. A rename that lands WHILE generation is still resolving the entity used
    to leave the snapshot written under the old name while the read model looked it up under
    the new name -> entity showed null. Domain-keying closes that window."""
    gate = threading.Event()
    entered = threading.Event()

    class GatedEntityLLM:
        """Blocks inside entity resolution until the test opens the gate, so the test can
        rename the vendor before the snapshot is written."""

        def structured(self, prompt, schema):
            if schema is EntityCard:
                entered.set()
                assert gate.wait(timeout=10), "gate never opened"
                return EntityCard(name="Voith", domain="voith.com", country="germany",
                                  industry="hydro", is_public=False)
            return Section(dimension=Dimension.LEGAL, findings=[], reasoning="x", score=5)

    deps = Deps(search=FakeSearch(), llm=GatedEntityLLM(), cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    client = TestClient(create_app(deps))
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Voith"}).json()["id"]

    def read_stream():
        with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
            for _ in resp.iter_lines():
                pass

    t = threading.Thread(target=read_stream, daemon=True)
    t.start()
    assert entered.wait(timeout=5), "generation never reached entity resolution"

    # rename BEFORE the snapshot is written (generation is blocked in resolution)
    assert client.patch(f"/vendors/{vid}", json={"name": "Voith SE"}).status_code == 200
    gate.set()
    t.join(timeout=10)

    report = client.get(f"/vendors/{vid}/report").json()
    assert report["generated"] is True
    assert report["entity"] is not None, "entity snapshot was orphaned by the rename"
    assert report["entity"]["domain"] == "voith.com"


class TwoVendorSameDomainLLM:
    """Resolves any name to the SAME domain, gates section synthesis so two generations are
    provably in-flight at once, and counts section calls so a test can prove the expensive
    work ran only once per domain (not once per vendor)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.section_calls = 0
        self.resolved = 0
        self.both_resolved = threading.Event()
        self.section_gate = threading.Event()

    def structured(self, prompt, schema):
        if schema is EntityCard:
            with self._lock:
                self.resolved += 1
                if self.resolved >= 2:
                    self.both_resolved.set()   # both generations are past resolution
            return EntityCard(name="Voith", domain="voith.com", country="germany",
                              industry="hydro", is_public=False)
        # section synthesis blocks until released, so without the domain lock BOTH
        # generations would be mid-section at the same time and both would count.
        assert self.section_gate.wait(timeout=30), "section gate never opened"
        with self._lock:
            self.section_calls += 1
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="x", score=5)


def test_two_vendors_same_domain_generate_sections_only_once(tmp_path):
    """Fix 1: single-flight is by resolved DOMAIN, not vendor_id. Two vendor rows whose
    names resolve to the same domain must not both run the expensive Tavily/LLM section
    work -- the per-domain lock serializes them so the second finds everything cached."""
    llm = TwoVendorSameDomainLLM()
    deps = Deps(search=FakeSearch(), llm=llm, cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    client = TestClient(create_app(deps))
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    a = client.post(f"/projects/{pid}/vendors", json={"name": "Voith"}).json()["id"]
    b = client.post(f"/projects/{pid}/vendors", json={"name": "Voith Hydro"}).json()["id"]

    def drain(vid):
        with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
            "".join(resp.iter_text())

    ta = threading.Thread(target=drain, args=(a,), daemon=True)
    tb = threading.Thread(target=drain, args=(b,), daemon=True)
    ta.start()
    tb.start()
    # wait until BOTH generations have resolved their entity and are contending for the
    # section phase, THEN release synthesis — this forces the real concurrent race.
    assert llm.both_resolved.wait(timeout=15), "both generations never resolved"
    llm.section_gate.set()
    ta.join(timeout=30)
    tb.join(timeout=30)

    # 6 = 5 Tavily dims + backlog, for ONE generation. Without the per-domain lock both
    # vendors would generate independently and this would be ~12.
    assert llm.section_calls == 6, f"expected one generation's worth, got {llm.section_calls}"
    assert client.get(f"/vendors/{a}/report").json()["generated"] is True
    assert client.get(f"/vendors/{b}/report").json()["generated"] is True


def test_chosen_patch_round_trip(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    r = client.patch(f"/vendors/{vid}/chosen", json={"chosen": True})
    assert r.status_code == 200 and r.json()["chosen"] is True

    detail = client.get(f"/projects/{pid}").json()
    assert detail["vendors"][0]["chosen"] is True

    r = client.patch(f"/vendors/{vid}/chosen", json={"chosen": False})
    assert r.json()["chosen"] is False


def test_chosen_patch_unknown_vendor_404(tmp_path):
    client = _client(tmp_path)
    assert client.patch("/vendors/9999/chosen", json={"chosen": True}).status_code == 404


def _generate(client, pid, name):
    """Add a vendor and drive its stream to completion so a report is cached."""
    vid = client.post(f"/projects/{pid}/vendors", json={"name": name}).json()["id"]
    with client.stream("GET", f"/vendors/{vid}/report/stream") as r:
        for _ in r.iter_lines():
            pass
    return vid


def test_stats_empty_when_no_projects(tmp_path):
    client = _client(tmp_path)
    d = client.get("/stats").json()
    assert d["vendors_total"] == 0 and d["avg_verdict"] is None


def test_stats_counts_generated_vendor(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    _generate(client, pid, "Cives Steel")
    d = client.get("/stats").json()
    assert d["vendors_total"] == 1 and d["vendors_generated"] == 1
    assert d["avg_verdict"] is not None
    assert len(d["verdict_histogram"]) == 11


def test_stats_filters_by_projects_param(tmp_path):
    client = _client(tmp_path)
    a = client.post("/projects", json={"name": "a"}).json()["id"]
    b = client.post("/projects", json={"name": "b"}).json()["id"]
    _generate(client, a, "Cives Steel")
    _generate(client, b, "Other Vendor")
    only_a = client.get(f"/stats?projects={a}").json()
    assert only_a["vendors_total"] == 1 and only_a["projects_selected"] == 1


def test_stats_unknown_project_ids_ignored(tmp_path):
    client = _client(tmp_path)
    a = client.post("/projects", json={"name": "a"}).json()["id"]
    _generate(client, a, "Cives Steel")
    d = client.get(f"/stats?projects={a},9999").json()
    assert d["vendors_total"] == 1   # 9999 silently dropped


def test_stats_malformed_projects_param_422(tmp_path):
    client = _client(tmp_path)
    assert client.get("/stats?projects=abc").status_code == 422


def test_report_carries_trust_counts(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = _generate(client, pid, "Cives Steel")
    client.patch(f"/vendors/{vid}/chosen", json={"chosen": True})
    r = client.get(f"/vendors/{vid}/report").json()
    assert r["chosen_count"] == 1 and r["projects_count"] == 1
    # deltas present as a dict (no prior day yet -> values may be null)
    assert "dimension_deltas" in r


def test_stream_generation_actually_writes_to_score_history(tmp_path):
    from vendor_dd.engine.history import ScoreHistory

    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = _generate(client, pid, "Cives Steel")
    vendor_key = client.get(f"/vendors/{vid}/report").json()["vendor_key"]
    history = ScoreHistory(tmp_path / "db.sqlite")
    scores = history.scores_for(vendor_key)
    assert "verdict" in scores  # proves stream_report's engine actually recorded to history
