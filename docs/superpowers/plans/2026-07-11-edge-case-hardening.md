# Edge-Case Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all eight lifecycle edge cases: duplicate-add race (DB-enforced uniqueness), zombie report generation after a vendor delete (cache resurrection), duplicate concurrent streams for one vendor (double API spend), the frontend's stale "delete and re-add" advice on a dropped SSE stream (poll instead), blank-name validation, project delete with cascading refcounted cache eviction, vendor rename without losing research, a resume/retry affordance for partial or failed reports, and a badge for domain-level duplicates.

**Architecture:** Backend gets (a) a `UNIQUE` expression index on `vendors(project_id, LOWER(TRIM(name)))` with an `IntegrityError` fallback in the add route, (b) a public `Store.evict_unreferenced` used by the generation thread to clean up cache written after its vendor was deleted, and (c) a `RunRegistry`/`ReportRun` fan-out so at most one generation runs per vendor — extra SSE subscribers replay history and tail the same run. Frontend replaces "mark failed on stream drop" with polling `GET /vendors/{id}/report` until `generated` flips true (the backend finishes regardless of the stream since the Phase-4 decoupling).

**Design decision (zombie generation):** we do NOT cancel mid-flight work. The pipeline runs dimensions in parallel, so by the time a delete could be observed, all Tavily/LLM spend is already committed; cancelling mid-generator also leaves the engine's internal executor still writing to the cache, which is exactly the race we're fixing. Instead the generation thread drains to completion and then, if its vendor is gone, evicts the cache it wrote (same refcount rules as delete). Deterministic cleanup over marginal credit savings.

**Tech Stack:** FastAPI + sqlite3 + sse-starlette (backend), React + Vitest + Testing Library (frontend). Repo root: `/Users/utkarsh/Desktop/Utkarsh/interviews/Tavily`.

**Commands:**
- Backend tests: `cd backend && .venv/bin/python -m pytest tests/ -q`
- Frontend tests: `cd frontend && npx vitest run`

---

### Task 1: DB-enforced vendor uniqueness per project

The current dedup is check-then-insert (`find_vendor` then `add_vendor`) with no constraint — two concurrent adds of the same name both pass the check. Add a unique expression index so the race is impossible, and make the route treat a lost race like the normal "already exists" path.

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/store.py` (Store.__init__, ~line 62)
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py` (add_vendor, ~line 86)
- Test: `backend/tests/test_store.py`, `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing store test**

Append to `backend/tests/test_store.py`:

```python
def test_store_enforces_unique_vendor_name_per_project(tmp_path):
    import sqlite3

    import pytest

    store = _store(tmp_path)
    p = store.create_project("p")
    store.add_vendor(p.id, "Cives Steel")
    with pytest.raises(sqlite3.IntegrityError):
        store.add_vendor(p.id, "  cives STEEL ")   # case/space variant of the same name
    # the same name in a different project is a legitimate new row
    p2 = store.create_project("p2")
    assert store.add_vendor(p2.id, "Cives Steel").id > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_store.py::test_store_enforces_unique_vendor_name_per_project -q`
Expected: FAIL — `DID NOT RAISE sqlite3.IntegrityError`

- [ ] **Step 3: Add the unique index in Store.__init__**

In `backend/src/vendor_dd/surfaces/api/store.py`, immediately after the `CREATE TABLE IF NOT EXISTS vendors (...)` `self._exec(...)` call and before `self._conn.commit()`:

```python
        # One row per (project, name) — normalized the same way find_vendor matches.
        # Enforced in the DB so a concurrent double-add can't slip past the
        # check-then-insert in the route. An old DB that already holds duplicates
        # would make index creation fail; keep serving in that case (the route-level
        # check still guards all new adds).
        try:
            self._exec(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_vendors_project_name "
                "ON vendors(project_id, LOWER(TRIM(name)))")
        except sqlite3.IntegrityError:
            pass
```

- [ ] **Step 4: Run store tests**

Run: `cd backend && .venv/bin/python -m pytest tests/test_store.py -q`
Expected: all PASS

- [ ] **Step 5: Write the failing route-race test**

Append to `backend/tests/test_api.py`:

```python
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
```

- [ ] **Step 6: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_add_vendor_lost_race_falls_back_to_the_existing_row -q`
Expected: FAIL — the POST raises `sqlite3.IntegrityError` (500)

- [ ] **Step 7: Add the IntegrityError fallback to the route**

In `backend/src/vendor_dd/surfaces/api/routes.py`, add `import sqlite3` to the imports (with the other stdlib imports at the top), add a small helper above `add_vendor`, and rewrite `add_vendor`:

```python
def _vendor_out(v: Vendor, *, existed: bool) -> VendorOut:
    return VendorOut(id=v.id, project_id=v.project_id, name=v.name,
                     vendor_key=v.vendor_key, created_at=v.created_at, existed=existed)


@router.post("/projects/{project_id}/vendors", response_model=VendorOut)
def add_vendor(project_id: int, body: VendorIn, request: Request):
    store = _store(request)
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    # Idempotent add: the same vendor name in this project returns the existing row
    # (no duplicate, no re-triggered research) instead of erroring or inserting again.
    existing = store.find_vendor(project_id, body.name)
    if existing is not None:
        return _vendor_out(existing, existed=True)
    try:
        v = store.add_vendor(project_id, body.name)
    except sqlite3.IntegrityError:
        # lost a race with a concurrent identical add — return the winner's row
        winner = store.find_vendor(project_id, body.name)
        if winner is None:   # can't happen: the constraint that fired proves the row exists
            raise HTTPException(status_code=409, detail="vendor already added")
        return _vendor_out(winner, existed=True)
    return _vendor_out(v, existed=False)
```

- [ ] **Step 8: Run the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/store.py backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_store.py backend/tests/test_api.py
git commit -m "fix: enforce vendor uniqueness per project in the DB, not just the route

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Public `Store.evict_unreferenced`

The generation thread (Task 3) needs to run the same refcounted cache eviction that delete uses, without reaching into the private `_clear_cache`. Thin public wrapper + its own commit.

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/store.py` (after `remove_vendor`, ~line 138)
- Test: `backend/tests/test_store.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_store.py`:

```python
def _seed_cache(tmp_path, key: str):
    from datetime import date

    from vendor_dd.engine.cache import SQLiteCache
    from vendor_dd.engine.schemas import Dimension, Section

    cache = SQLiteCache(tmp_path / "db.sqlite")
    cache.put(key, Dimension.LEGAL,
              Section(dimension=Dimension.LEGAL, findings=[], reasoning="x", score=5).model_dump(mode="json"))
    cache.close()


def _cached_sections(tmp_path, key: str):
    from vendor_dd.engine.cache import SQLiteCache

    cache = SQLiteCache(tmp_path / "db.sqlite")
    rows = cache.all_sections(key)
    cache.close()
    return rows


def test_evict_unreferenced_clears_cache_when_no_vendor_row_references_it(tmp_path):
    store = _store(tmp_path)
    _seed_cache(tmp_path, "cives.com")
    store.evict_unreferenced("Cives Steel", "cives.com")
    assert _cached_sections(tmp_path, "cives.com") == {}


def test_evict_unreferenced_keeps_cache_while_a_vendor_still_references_it(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Cives Steel")
    store.set_vendor_key(v.id, "cives.com")
    _seed_cache(tmp_path, "cives.com")
    store.evict_unreferenced("Cives Steel", "cives.com")   # still referenced -> no-op
    assert _cached_sections(tmp_path, "cives.com") != {}
```

Note: if `SQLiteCache.put` has a different signature in `backend/src/vendor_dd/engine/cache.py`, match the call shape used by `backend/tests/test_api.py::test_read_model_reports_section_completeness` (it seeds the cache the same way).

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_store.py -k evict_unreferenced -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'evict_unreferenced'`

- [ ] **Step 3: Implement**

In `backend/src/vendor_dd/surfaces/api/store.py`, after `remove_vendor`:

```python
    def evict_unreferenced(self, name: str, vendor_key: str | None) -> None:
        """Evict this vendor's cached research unless another vendor row still
        references it — same refcount rules as delete. Used by report generation
        when it finishes after its vendor was deleted mid-run."""
        self._clear_cache(name, vendor_key)
        self._conn.commit()
```

- [ ] **Step 4: Run and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_store.py -q`
Expected: all PASS

```bash
git add backend/src/vendor_dd/surfaces/api/store.py backend/tests/test_store.py
git commit -m "feat: public refcounted cache eviction on Store

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `ReportRun` / `RunRegistry` fan-out module

New module implementing "at most one generation per vendor": a `ReportRun` fans events out to any number of subscriber queues (late subscribers get history replayed), a `RunRegistry` maps vendor_id → in-flight run. Pure unit-tested plumbing; wired into routes in Task 4.

**Files:**
- Create: `backend/src/vendor_dd/surfaces/api/runs.py`
- Test: `backend/tests/test_runs.py` (new file)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_runs.py`:

```python
from vendor_dd.surfaces.api.runs import DONE, ReportRun, RunRegistry


def _drain(q):
    out = []
    while True:
        ev = q.get(timeout=1)
        if ev is DONE:
            return out
        out.append(ev)


def test_subscriber_receives_published_events_then_done():
    run = ReportRun()
    q = run.subscribe()
    run.publish("a")
    run.publish("b")
    run.finish()
    assert _drain(q) == ["a", "b"]


def test_late_subscriber_gets_history_replayed_before_live_events():
    run = ReportRun()
    run.publish("a")
    q = run.subscribe()          # joins after "a" was published
    run.publish("b")
    run.finish()
    assert _drain(q) == ["a", "b"]


def test_subscribing_after_finish_yields_full_history_then_done():
    run = ReportRun()
    run.publish("a")
    run.finish()
    assert _drain(run.subscribe()) == ["a"]


def test_registry_returns_the_same_run_while_in_flight():
    reg = RunRegistry()
    run1, created1 = reg.get_or_create(7)
    run2, created2 = reg.get_or_create(7)
    assert created1 is True and created2 is False and run1 is run2
    reg.remove(7)
    run3, created3 = reg.get_or_create(7)
    assert created3 is True and run3 is not run1


def test_registry_remove_is_idempotent():
    reg = RunRegistry()
    reg.remove(42)   # never registered — must not raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_runs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vendor_dd.surfaces.api.runs'`

- [ ] **Step 3: Implement the module**

Create `backend/src/vendor_dd/surfaces/api/runs.py`:

```python
from __future__ import annotations

import queue
import threading

DONE = object()   # terminal sentinel every subscriber receives exactly once


class ReportRun:
    """Fan-out for one in-flight report generation: the generator thread publishes
    events, any number of SSE subscribers tail them. A subscriber that joins late
    gets the full history replayed first, so a second browser tab sees the report
    from the beginning instead of only the remaining frames."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: list[object] = []
        self._subscribers: list[queue.Queue] = []
        self._done = False

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for ev in self._history:
                q.put(ev)
            if self._done:
                q.put(DONE)
            else:
                self._subscribers.append(q)
        return q

    def publish(self, ev: object) -> None:
        with self._lock:
            self._history.append(ev)
            for q in self._subscribers:
                q.put(ev)

    def finish(self) -> None:
        with self._lock:
            self._done = True
            for q in self._subscribers:
                q.put(DONE)
            self._subscribers.clear()


class RunRegistry:
    """At most one live generation per vendor. get_or_create is atomic: the caller
    that gets created=True owns starting the worker thread; everyone else tails."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[int, ReportRun] = {}

    def get_or_create(self, vendor_id: int) -> tuple[ReportRun, bool]:
        with self._lock:
            run = self._runs.get(vendor_id)
            if run is not None:
                return run, False
            run = ReportRun()
            self._runs[vendor_id] = run
            return run, True

    def remove(self, vendor_id: int) -> None:
        with self._lock:
            self._runs.pop(vendor_id, None)
```

- [ ] **Step 4: Run and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/test_runs.py -q`
Expected: 5 PASS

```bash
git add backend/src/vendor_dd/surfaces/api/runs.py backend/tests/test_runs.py
git commit -m "feat: ReportRun/RunRegistry fan-out for single-flight report generation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the registry into the stream route + post-delete cache cleanup

Rewrite `stream_report` so (a) only the first stream for a vendor starts a generation thread — later streams subscribe to the same run, and (b) when generation finishes and its vendor is gone (deleted mid-run), the thread evicts the cache it wrote. This kills both the double-spend and the cache-resurrection race.

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/app.py` (create_app)
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py` (stream_report, ~line 136; drop `_STREAM_DONE`)
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing single-flight test**

Append to `backend/tests/test_api.py`:

```python
import threading


class GatedLLM:
    """Entity resolution is instant; section synthesis blocks on a gate the test
    controls, so generation stays in flight for as long as the test needs."""

    def __init__(self):
        self.gate = threading.Event()
        self.entity_calls = 0

    def structured(self, prompt, schema):
        if schema is EntityCard:
            self.entity_calls += 1
            return EntityCard(name="Cives Steel", domain="cives.com", country="united states",
                              industry="steel", is_public=False)
        assert self.gate.wait(timeout=10), "test never opened the gate"
        return Section(dimension=Dimension.LEGAL, findings=[], reasoning="x", score=5)


def test_second_stream_for_the_same_vendor_tails_the_existing_run(tmp_path):
    """Two concurrent streams must NOT start two generations (double Tavily/LLM
    spend, racing cache writes). The second subscriber replays history and tails."""
    llm = GatedLLM()
    deps = Deps(search=FakeSearch(), llm=llm, cache_path=tmp_path / "db.sqlite",
                today=date(2026, 7, 8), fetch_transcript=lambda url: (None, None))
    client = TestClient(create_app(deps))
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    with client.stream("GET", f"/vendors/{vid}/report/stream") as first:
        # wait until generation is provably in flight (entity frame emitted)
        lines1 = first.iter_lines()
        for line in lines1:
            if line.startswith("event:") and "entity_resolved" in line:
                break
        with client.stream("GET", f"/vendors/{vid}/report/stream") as second:
            llm.gate.set()   # let sections finish; both streams should now drain
            body2 = "".join(second.iter_text())
        rest1 = "".join(first.iter_text())

    assert llm.entity_calls == 1                       # one generation, not two
    names2 = _event_names(body2)
    assert names2[0] == "entity_resolved"              # history was replayed
    assert names2[-1] == "report_complete"
    assert _event_names(rest1)[-1] == "report_complete"
```

(`EntityCard`, `Section`, `Dimension`, `Deps`, `date`, `TestClient`, `create_app`, `FakeSearch`, `_event_names` are already imported/defined in this file.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_second_stream_for_the_same_vendor_tails_the_existing_run -q`
Expected: FAIL — `llm.entity_calls == 2` (two independent generations today)

If the nested `client.stream` blocks (TestClient portal limitation), fall back to reading the second stream on a `threading.Thread` and joining it — the assertions stay identical.

- [ ] **Step 3: Write the failing delete-mid-generation test**

Append to `backend/tests/test_api.py`:

```python
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
    client = TestClient(create_app(deps))
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    with client.stream("GET", f"/vendors/{vid}/report/stream") as resp:
        for line in resp.iter_lines():
            if line.startswith("event:") and "entity_resolved" in line:
                break   # vendor_key backfilled; all sections still gated

    client.delete(f"/vendors/{vid}")   # runs its own eviction; the run is still gated
    llm.gate.set()                     # sections now synthesize and hit the cache

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
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_delete_mid_generation_evicts_the_cache_the_zombie_run_writes -q`
Expected: FAIL — sections re-appear in the cache and stay (timeout, `sections != {}`)

- [ ] **Step 5: Register the RunRegistry on the app**

In `backend/src/vendor_dd/surfaces/api/app.py`, inside `create_app`, next to the other `app.state` assignments:

```python
    from vendor_dd.surfaces.api.runs import RunRegistry
    app.state.runs = RunRegistry()
```

- [ ] **Step 6: Rewrite stream_report**

In `backend/src/vendor_dd/surfaces/api/routes.py`:

1. Delete the `_STREAM_DONE = object()` line (~line 26) and the `import queue` line (no longer used here).
2. Add to the imports: `from vendor_dd.surfaces.api.runs import DONE, RunRegistry`
3. Replace the whole `stream_report` function with:

```python
@router.get("/vendors/{vendor_id}/report/stream")
def stream_report(vendor_id: int, request: Request):
    store = _store(request)
    vendor = store.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="vendor not found")

    # Single-flight per vendor: only the caller that CREATES the run starts a
    # generation thread. A second stream (another tab, a refresh) subscribes to
    # the same run — history replayed, then live events — instead of kicking off
    # a duplicate generation that would double Tavily/LLM spend and race the cache.
    registry: RunRegistry = request.app.state.runs
    run, created = registry.get_or_create(vendor_id)

    if created:
        project = store.get_project(vendor.project_id)
        session_id = project.session_id if project else None
        engine = ReportEngine(request.app.state.deps, mode="parallel", session_id=session_id)

        # Decouple the WORK from the STREAM. Generation runs in a background thread
        # and drains to completion regardless of listeners; the pipeline caches each
        # section as it lands, so a broken SSE still yields a finished, cached report.
        def generate() -> None:
            key: str | None = None
            try:
                for ev in engine.iter_events(vendor.name):
                    if isinstance(ev, EntityResolved):
                        key = (ev.entity.domain or ev.entity.name).strip().lower()
                        if store.get_vendor(vendor_id) is not None:
                            store.set_vendor_key(vendor_id, key)  # backfill for the read model
                    run.publish(ev)
            finally:
                # The vendor may have been deleted while we worked. Its DELETE already
                # evicted the cache — but we kept writing sections afterwards. Now that
                # every write is done, evict again (refcount rules still apply, so a
                # same-key vendor in another project keeps its report).
                if store.get_vendor(vendor_id) is None:
                    store.evict_unreferenced(vendor.name, key)
                registry.remove(vendor_id)
                run.finish()

        # copy_context so the request's correlation id follows the work into the thread
        ctx = contextvars.copy_context()
        threading.Thread(target=lambda: ctx.run(generate),
                         name=f"report-{vendor_id}", daemon=True).start()

    subscription = run.subscribe()

    def event_source():
        while True:
            ev = subscription.get()
            if ev is DONE:
                break
            yield to_sse_frame(ev)

    return EventSourceResponse(event_source())
```

- [ ] **Step 7: Run the two new tests, then the full backend suite**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py -q && cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS (the pre-existing stream tests — full sequence, disconnect-resilience, section_error — must still pass; they exercise the same route through the new fan-out).

- [ ] **Step 8: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/app.py backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_api.py
git commit -m "fix: single-flight report generation per vendor; clean up cache when a run outlives its vendor

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `rowFromReport` — build a done row from the read model

The frontend needs to turn a polled `GET /vendors/{id}/report` payload (a `VendorReport`) into a finished `RowState`. Pure function in `rows.ts`, mirroring what `report_complete` does for SSE.

**Files:**
- Modify: `frontend/src/rows.ts`
- Test: `frontend/src/test/rows.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/test/rows.test.ts` (import `rowFromReport` alongside the existing imports from `../rows`, and `VendorReport` from `../types`):

```typescript
const streamingRow = (): RowState => ({
  vendorId: 5, name: 'Cives Steel', vendorKey: null,
  cells: { legal: 'pending', financial: 'pending', safety: 'pending',
           certifications: 'pending', backlog: 'pending', news: 'pending' },
  verdict: 'pending', status: 'streaming',
})

const polled: VendorReport = {
  generated: true, vendor_key: 'cives.com',
  entity: { name: 'Cives Steel', domain: 'cives.com', country: null, industry: null,
            parent: null, is_public: false, ticker: null, exchange: null },
  verdict_score: 7, verdict_reasoning: 'Solid.',
  sections: [{ dimension: 'legal', findings: [], reasoning: 'clean', score: 8 }],
  sections_present: 1, sections_expected: 6,
}

test('rowFromReport finishes a streaming row from the polled read model', () => {
  const row = rowFromReport(streamingRow(), polled)
  expect(row.status).toBe('done')
  expect(row.cells.legal).toEqual({ score: 8 })
  expect(row.cells.financial).toBe('failed')          // absent section -> failed, not pending
  expect(row.verdict).toEqual({ score: 7 })
  expect(row.report?.verdict_reasoning).toBe('Solid.')
  expect(row.sectionsPresent).toBe(1)
})

test('rowFromReport with no verdict leaves the report panel data unset but ends the row', () => {
  const partial: VendorReport = { ...polled, verdict_score: null, verdict_reasoning: null }
  const row = rowFromReport(streamingRow(), partial)
  expect(row.status).toBe('done')
  expect(row.verdict).toBe('failed')
  expect(row.report).toBeUndefined()
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/test/rows.test.ts`
Expected: FAIL — `rowFromReport` is not exported

- [ ] **Step 3: Implement**

In `frontend/src/rows.ts`, add `VendorReport` to the type import from `./types`, then append:

```typescript
/** Finish a row from the polled read model — the poll-path twin of report_complete. */
export function rowFromReport(row: RowState, r: VendorReport): RowState {
  const complete = r.verdict_score != null && r.verdict_reasoning != null && r.entity != null
  return {
    ...row,
    status: 'done',
    entity: r.entity ?? row.entity,
    cells: cellsFromSections(r.sections),
    verdict: r.verdict_score == null ? 'failed' : { score: r.verdict_score },
    report: complete ? {
      vendor_input: row.name, entity: r.entity!, sections: r.sections,
      verdict_score: r.verdict_score!, verdict_reasoning: r.verdict_reasoning!,
    } : undefined,
    sectionsPresent: r.sections_present,
    sectionsExpected: r.sections_expected,
  }
}
```

- [ ] **Step 4: Run and commit**

Run: `cd frontend && npx vitest run src/test/rows.test.ts`
Expected: all PASS

```bash
git add frontend/src/rows.ts frontend/src/test/rows.test.ts
git commit -m "feat: rowFromReport builds a finished row from the polled read model

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Poll on stream drop instead of failing the row

Since the backend finishes and caches the report even when the SSE dies, the frontend's "mark failed + tell the user to delete and re-add" is actively wrong — following that advice evicts a report that's completing fine. On stream error: keep the row streaming and poll `GET /report` (immediately, then every 3s) until `generated` is true; only give up after ~5 minutes.

**Files:**
- Modify: `frontend/src/App.tsx` (reducer + addVendor onError + new pollReport)
- Test: `frontend/src/test/app.test.tsx` (replace the existing dropped-stream test)

- [ ] **Step 1: Rewrite the dropped-stream test (failing)**

In `frontend/src/test/app.test.tsx`, replace the test `'a dropped SSE stream marks the row failed instead of leaving it pending forever'` (~line 111) with:

```typescript
const completedReport = {
  generated: true, vendor_key: 'cives.com',
  entity: { name: 'Cives Steel', domain: 'cives.com', country: null, industry: null,
            parent: null, is_public: false, ticker: null, exchange: null },
  verdict_score: 7, verdict_reasoning: 'Solid.',
  sections: [{ dimension: 'legal', score: 8, findings: [], reasoning: 'clean' }],
  sections_present: 1, sections_expected: 6,
}

test('a dropped SSE stream falls back to polling the report, not failing the row', async () => {
  // The backend finishes and caches the report even when the stream dies —
  // the row must recover via GET /report instead of telling the user to
  // delete and re-add (which would evict the completing report).
  mockApi({ vendorReports: { 5: completedReport } })
  render(<App />)

  await screen.findByRole('heading', { name: 'Bridge job' })
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  es.emit('entity_resolved', { entity: { name: 'Cives Steel', domain: 'cives.com' } })
  es.fail()   // stream drops; the immediate poll finds the finished report

  await waitFor(() => expect(screen.getByText('7/10')).toBeInTheDocument())
  expect(screen.queryByText(/stream dropped/i)).toBeNull()
  expect(document.querySelector('.vendor-row .failed')).toBeNull()
})

test('while the polled report is still generating, the row keeps streaming', async () => {
  mockApi({ vendorReports: { 5: { ...completedReport, generated: false, sections: [],
                                   verdict_score: null, verdict_reasoning: null, entity: null } } })
  render(<App />)

  await screen.findByRole('heading', { name: 'Bridge job' })
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  es.emit('entity_resolved', { entity: { name: 'Cives Steel', domain: 'cives.com' } })
  es.fail()

  // the immediate poll returns generated:false -> still streaming, no failure UI
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).endsWith('/vendors/5/report'))).toBe(true))
  expect(document.querySelector('.vendor-row .dot')).not.toBeNull()
  expect(screen.queryByText(/stream dropped/i)).toBeNull()
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/test/app.test.tsx`
Expected: the two new tests FAIL (row is marked failed today; "stream dropped" banner appears)

- [ ] **Step 3: Implement pollReport in App.tsx**

In `frontend/src/App.tsx`:

1. Add to the type imports: `import type { VendorReport } from './types'` (merge into the existing `./types` import) and add `rowFromReport` to the `./rows` import.
2. Add a reducer action and case:

```typescript
  | { kind: 'fromReport'; vendorId: number; report: VendorReport }
```

```typescript
    case 'fromReport':
      return state.map((r) =>
        r.vendorId === action.vendorId ? rowFromReport(r, action.report) : r)
```

3. Add constants above the `App` component and `pollReport` inside it (next to `removeVendor`):

```typescript
const POLL_MS = 3000
const MAX_POLLS = 100   // ~5 minutes of polling before we give up
```

```typescript
  // The backend keeps generating (and caching) even when the SSE stream dies, so a
  // dropped stream is not a failure — poll the read model until the report lands.
  function pollReport(vendorId: number) {
    let attempts = 0
    const id = window.setInterval(() => void check(), POLL_MS)
    const stop = () => {
      window.clearInterval(id)
      streams.current.delete(vendorId)
    }
    async function check() {
      attempts += 1
      try {
        const r = await api.getReport(vendorId)
        if (r.generated) {
          stop()
          dispatch({ kind: 'fromReport', vendorId, report: r })
          return
        }
      } catch {
        // transient (backend restarting, network blip) — keep polling
      }
      if (attempts >= MAX_POLLS) {
        stop()
        dispatch({ kind: 'event', vendorId, ev: { type: 'report_error', message: 'report generation stalled' } })
        setError('Report generation stalled — press ⟳ on the row to retry.')   // the ⟳ button lands in Task 10
      }
    }
    streams.current.set(vendorId, () => window.clearInterval(id))   // project-switch/unmount cleanup
    void check()   // immediate first check: the backend may already be done
  }
```

4. Replace the `onError` callback inside `addVendor`'s `openReportStream` call:

```typescript
        () => {
          // stream dropped, but the work continues server-side — recover via polling
          streams.current.delete(v.id)
          pollReport(v.id)
        },
```

- [ ] **Step 4: Run the frontend suite**

Run: `cd frontend && npx vitest run`
Expected: all PASS (if another existing test asserted the old "stream dropped" failure behavior, update it to the polling expectation the same way as Step 1).

- [ ] **Step 5: Build check and commit**

Run: `cd frontend && npx tsc -b && npx vitest run`
Expected: clean build, all tests PASS

```bash
git add frontend/src/App.tsx frontend/src/test/app.test.tsx
git commit -m "fix: recover from a dropped report stream by polling instead of failing the row

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Reject blank names, store them trimmed

The API accepts `"   "` as a project or vendor name today (the form blocks it client-side, but curl/tests don't). Validate in the routes and store the trimmed value. Storing trimmed names also removes the flagged normalization mismatch in `_clear_cache` (`.strip().lower()` key vs SQL `LOWER(name)` without TRIM) for all new rows.

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py` (create_project, add_vendor)
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api.py`:

```python
def test_blank_names_are_rejected_and_stored_names_are_trimmed(tmp_path):
    client = _client(tmp_path)
    assert client.post("/projects", json={"name": "   "}).status_code == 422
    pid = client.post("/projects", json={"name": "  Bridge job  "}).json()["id"]
    assert client.get(f"/projects/{pid}").json()["name"] == "Bridge job"

    assert client.post(f"/projects/{pid}/vendors", json={"name": " \t "}).status_code == 422
    v = client.post(f"/projects/{pid}/vendors", json={"name": "  Cives Steel  "}).json()
    assert v["name"] == "Cives Steel"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_blank_names_are_rejected_and_stored_names_are_trimmed -q`
Expected: FAIL — blank names return 200 and names are stored with whitespace

- [ ] **Step 3: Validate and trim in the routes**

In `backend/src/vendor_dd/surfaces/api/routes.py`, add a helper above `create_project` and use it in both write routes:

```python
def _clean_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    return name
```

In `create_project`, replace the body of the function's first line usage:

```python
@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectIn, request: Request):
    p = _store(request).create_project(_clean_name(body.name))
    return ProjectOut(id=p.id, name=p.name, created_at=p.created_at)
```

In `add_vendor`, right after the project-404 check, add `name = _clean_name(body.name)` and use `name` (not `body.name`) in the `find_vendor` and `add_vendor` calls (both the main path and the `IntegrityError` fallback).

- [ ] **Step 4: Run the backend suite and commit**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS

```bash
git add backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_api.py
git commit -m "fix: reject blank project/vendor names and store them trimmed

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Project delete with cascading, refcounted cache eviction

Projects can only be created today — they accumulate forever. Add `DELETE /projects/{id}` that removes the project's vendors through the existing `remove_vendor` (so each one gets the refcounted cache eviction), then a delete button in the sidebar.

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/store.py` (after `get_project`)
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py` (after `get_project` route)
- Modify: `frontend/src/api.ts`, `frontend/src/components/Sidebar.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`
- Test: `backend/tests/test_store.py`, `backend/tests/test_api.py`, `frontend/src/test/app.test.tsx`

- [ ] **Step 1: Write the failing store test**

Append to `backend/tests/test_store.py` (reuses the `_seed_cache`/`_cached_sections` helpers from Task 2):

```python
def test_remove_project_cascades_vendors_and_evicts_unreferenced_cache(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Cives Steel")
    store.set_vendor_key(v.id, "cives.com")
    _seed_cache(tmp_path, "cives.com")

    # a vendor in ANOTHER project shares the cache key — its report must survive
    p2 = store.create_project("p2")
    v2 = store.add_vendor(p2.id, "Cives Steel")
    store.set_vendor_key(v2.id, "cives.com")

    store.remove_project(p.id)
    assert store.get_project(p.id) is None
    assert store.list_vendors(p.id) == []
    assert _cached_sections(tmp_path, "cives.com") != {}   # still referenced by p2

    store.remove_project(p2.id)
    assert _cached_sections(tmp_path, "cives.com") == {}   # last reference gone -> evicted
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_store.py::test_remove_project_cascades_vendors_and_evicts_unreferenced_cache -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'remove_project'`

- [ ] **Step 3: Implement Store.remove_project**

In `backend/src/vendor_dd/surfaces/api/store.py`, after `get_project`:

```python
    def remove_project(self, project_id: int) -> None:
        # Cascade through remove_vendor so every vendor gets the same refcounted
        # cache eviction a single delete gets.
        for v in self.list_vendors(project_id):
            self.remove_vendor(v.id)
        self._exec("DELETE FROM projects WHERE id=?", (project_id,))
        self._conn.commit()
```

- [ ] **Step 4: Write the failing route test**

Append to `backend/tests/test_api.py`:

```python
def test_delete_project_removes_it_and_its_vendors(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = client.post(f"/projects/{pid}/vendors", json={"name": "Cives Steel"}).json()["id"]

    assert client.delete(f"/projects/{pid}").status_code == 200
    assert client.get(f"/projects/{pid}").status_code == 404
    assert client.get(f"/vendors/{vid}/report").status_code == 404
    assert client.delete("/projects/9999").status_code == 404
```

- [ ] **Step 5: Run it, then implement the route**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_delete_project_removes_it_and_its_vendors -q` — expect FAIL (405).

In `backend/src/vendor_dd/surfaces/api/routes.py`, after the `get_project` route:

```python
@router.delete("/projects/{project_id}")
def delete_project(project_id: int, request: Request):
    store = _store(request)
    if store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    store.remove_project(project_id)
    return {"ok": True}
```

Run: `cd backend && .venv/bin/python -m pytest tests/ -q` — expect all PASS.

- [ ] **Step 6: Write the failing frontend test**

Append to `frontend/src/test/app.test.tsx`:

```typescript
test('deleting the active project removes it and falls back to another project', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const { fetchMock } = mockApi({
    projects: [
      { id: 1, name: 'Bridge job', created_at: 't' },
      { id: 2, name: 'Tunnel job', created_at: 't' },
    ],
    projectDetails: {
      1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [] },
      2: { id: 2, name: 'Tunnel job', created_at: 't', vendors: [] },
    },
  })
  render(<App />)
  await screen.findByRole('heading', { name: 'Bridge job' })

  await userEvent.click(screen.getByRole('button', { name: /delete project bridge job/i }))

  // DELETE fired, project gone from the sidebar, the other project took over
  await waitFor(() => expect(
    fetchMock.mock.calls.some(([u, init]) =>
      String(u).endsWith('/projects/1') && init?.method === 'DELETE')).toBe(true))
  await screen.findByRole('heading', { name: 'Tunnel job' })
  expect(screen.queryByText('Bridge job')).toBeNull()
})
```

Run: `cd frontend && npx vitest run src/test/app.test.tsx` — expect FAIL (no delete button).

- [ ] **Step 7: Implement api + Sidebar + App changes**

In `frontend/src/api.ts`, after `deleteVendor`:

```typescript
  deleteProject: (id: number) =>
    fetch(`${BASE}/projects/${id}`, { method: 'DELETE' }).then((r) => {
      if (r.status === 404) return undefined   // already gone -> desired outcome
      return ensureOk(r).then(() => undefined)
    }),
```

Replace `frontend/src/components/Sidebar.tsx` with:

```typescript
import type { Project } from '../types'
import { NewProjectForm } from './NewProjectForm'

export function Sidebar({
  projects, activeId, onSelect, onCreate, onDelete,
}: {
  projects: Project[]
  activeId: number | null
  onSelect: (id: number) => void
  onCreate: (name: string) => void
  onDelete: (id: number) => void
}) {
  return (
    <aside className="sidebar">
      <h4>Projects</h4>
      {projects.length === 0 ? (
        <p className="empty">Create a project to begin.</p>
      ) : (
        <ul className="project-list">
          {projects.map((p) => (
            <li
              key={p.id}
              className={p.id === activeId ? 'active' : ''}
              tabIndex={0}
              role="button"
              aria-pressed={p.id === activeId}
              onClick={() => onSelect(p.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(p.id) }
              }}
            >
              <span className="project-name">{p.name}</span>
              <button
                className="delete-btn project-delete"
                aria-label={`Delete project ${p.name}`}
                title="Delete project"
                onClick={(e) => { e.stopPropagation(); onDelete(p.id) }}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
                     stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                     strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 6h18" />
                  <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                  <path d="M10 11v6" />
                  <path d="M14 11v6" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}
      <NewProjectForm onCreate={onCreate} />
    </aside>
  )
}
```

In `frontend/src/App.tsx`, add next to `createProject`:

```typescript
  async function removeProject(projectId: number) {
    const p = projects.find((x) => x.id === projectId)
    if (!window.confirm(`Delete project "${p?.name ?? projectId}" and all its vendors?`)) return
    try {
      await api.deleteProject(projectId)
      const rest = projects.filter((x) => x.id !== projectId)
      setProjects(rest)
      if (activeId === projectId) {
        setActiveId(rest.length ? rest[0].id : null)
        if (!rest.length) dispatch({ kind: 'set', rows: [] })
      }
    } catch {
      setError('Could not delete the project.')
    }
  }
```

and pass it to the sidebar: `<Sidebar projects={projects} activeId={activeId} onSelect={setActiveId} onCreate={createProject} onDelete={removeProject} />`.

In `frontend/src/styles.css`, style the button (reveal on hover/focus, matching the vendor delete button):

```css
.project-list li { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.project-list .project-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.project-delete { opacity: 0; }
.project-list li:hover .project-delete,
.project-list li:focus-within .project-delete { opacity: 1; }
```

If `frontend/src/test/sidebar.test.tsx` renders `<Sidebar>` directly, add `onDelete={() => {}}` to its props to keep it compiling.

- [ ] **Step 8: Run both suites and commit**

Run: `cd frontend && npx tsc -b && npx vitest run` and `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS

```bash
git add backend/src/vendor_dd/surfaces/api/store.py backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_store.py backend/tests/test_api.py frontend/src/api.ts frontend/src/components/Sidebar.tsx frontend/src/App.tsx frontend/src/styles.css frontend/src/test/app.test.tsx
git commit -m "feat: project delete with cascading refcounted cache eviction

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Rename a vendor without losing its research

A typo currently forces delete + re-add, which evicts the cache and re-spends the whole research budget. Add `PATCH /vendors/{id}` that updates the name and migrates the name-keyed snapshot cache entry (sections are keyed by domain and are untouched). The Task 1 unique index turns a rename-collision into a 409.

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/store.py` (after `add_vendor`)
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py` (after `add_vendor` route)
- Modify: `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/components/VendorRow.tsx`, `frontend/src/components/VendorTable.tsx`, `frontend/src/styles.css`
- Test: `backend/tests/test_store.py`, `backend/tests/test_api.py`, `frontend/src/test/app.test.tsx`

- [ ] **Step 1: Write the failing store test**

Append to `backend/tests/test_store.py`:

```python
def test_rename_vendor_updates_the_name_and_migrates_the_snapshot_cache_key(tmp_path):
    store = _store(tmp_path)
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Cives Stel")           # typo
    store.set_vendor_key(v.id, "cives.com")
    _seed_cache(tmp_path, "cives stel")                 # snapshot lives under the NAME key
    _seed_cache(tmp_path, "cives.com")                  # sections live under the domain key

    renamed = store.rename_vendor(v.id, "Cives Steel")
    assert renamed is not None and renamed.name == "Cives Steel"
    assert _cached_sections(tmp_path, "cives stel") == {}          # old name key gone
    assert _cached_sections(tmp_path, "cives steel") != {}         # moved to the new name
    assert _cached_sections(tmp_path, "cives.com") != {}           # domain sections untouched
    assert store.rename_vendor(9999, "x") is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_store.py::test_rename_vendor_updates_the_name_and_migrates_the_snapshot_cache_key -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'rename_vendor'`

- [ ] **Step 3: Implement Store.rename_vendor**

In `backend/src/vendor_dd/surfaces/api/store.py`, after `add_vendor`:

```python
    def rename_vendor(self, vendor_id: int, name: str) -> Vendor | None:
        """Rename without losing research: sections are cached under the domain key
        (unchanged by a rename); only the snapshot entry is keyed by the normalized
        NAME, so move it to the new key — unless another vendor row still uses the
        old name, in which case it keeps its snapshot. Raises sqlite3.IntegrityError
        if the new name collides within the project (unique index)."""
        old = self.get_vendor(vendor_id)
        if old is None:
            return None
        self._exec("UPDATE vendors SET name=? WHERE id=?", (name, vendor_id))
        old_key, new_key = old.name.strip().lower(), name.strip().lower()
        has_cache = self._exec(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_cache'").fetchone()
        if has_cache and old_key != new_key:
            still_referenced = self._exec(
                "SELECT 1 FROM vendors WHERE LOWER(TRIM(name))=? LIMIT 1", (old_key,)).fetchone()
            if not still_referenced:
                # OR REPLACE: if the new name key somehow already has rows, take theirs over
                self._exec("UPDATE OR REPLACE report_cache SET vendor_key=? WHERE vendor_key=?",
                           (new_key, old_key))
        self._conn.commit()
        return self.get_vendor(vendor_id)
```

Run: `cd backend && .venv/bin/python -m pytest tests/test_store.py -q` — expect all PASS.

- [ ] **Step 4: Write the failing route test**

Append to `backend/tests/test_api.py`:

```python
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
```

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_rename_vendor_route -q` — expect FAIL (405).

- [ ] **Step 5: Implement the route**

In `backend/src/vendor_dd/surfaces/api/routes.py`, after the `add_vendor` route:

```python
@router.patch("/vendors/{vendor_id}", response_model=VendorOut)
def rename_vendor(vendor_id: int, body: VendorIn, request: Request):
    store = _store(request)
    name = _clean_name(body.name)
    try:
        v = store.rename_vendor(vendor_id, name)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409,
                            detail="a vendor with that name is already in this project")
    if v is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    return _vendor_out(v, existed=False)
```

Run: `cd backend && .venv/bin/python -m pytest tests/ -q` — expect all PASS.

- [ ] **Step 6: Write the failing frontend test**

Append to `frontend/src/test/app.test.tsx`:

```typescript
test('renaming a vendor PATCHes the API and updates the row in place', async () => {
  const doneVendor = {
    vendor_id: 9, name: 'Cives Stel', vendor_key: 'cives.com', generated: true,
    sections_present: 6, sections_expected: 6, verdict_score: 7, verdict_reasoning: 'ok',
    dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
  }
  const { fetchMock } = mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [doneVendor] } },
  })
  render(<App />)
  await screen.findByText('Cives Stel')

  await userEvent.click(screen.getByRole('button', { name: /rename vendor/i }))
  const input = screen.getByRole('textbox', { name: /vendor name/i })
  await userEvent.clear(input)
  await userEvent.type(input, 'Cives Steel{Enter}')

  await waitFor(() => expect(
    fetchMock.mock.calls.some(([u, init]) =>
      String(u).endsWith('/vendors/9') && init?.method === 'PATCH')).toBe(true))
  await screen.findByText('Cives Steel')
  expect(screen.queryByText('Cives Stel')).toBeNull()
})
```

Run: `cd frontend && npx vitest run src/test/app.test.tsx` — expect FAIL (no rename button).

- [ ] **Step 7: Implement api + row editing + wiring**

In `frontend/src/api.ts`, after `addVendor`:

```typescript
  renameVendor: (id: number, name: string) =>
    fetch(`${BASE}/vendors/${id}`, {
      method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ name }),
    }).then(json<VendorOut>),
```

Replace `frontend/src/components/VendorRow.tsx` with (adds an inline-edit state; the ⟳ resume button slot referenced here is filled in Task 10 — omit it for now):

```typescript
import { useState } from 'react'
import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { DimensionCell } from './DimensionCell'
import { bandForScore } from '../band'

export function VendorRow({
  row, onSelect, onDelete, onRename,
}: {
  row: RowState
  onSelect: (id: number) => void
  onDelete: (id: number) => void
  onRename: (id: number, name: string) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)   // non-null while editing
  const verdict = row.verdict
  const commit = () => {
    const name = (draft ?? '').trim()
    setDraft(null)
    if (name && name !== row.name) onRename(row.vendorId, name)
  }
  return (
    <tr
      className="vendor-row"
      tabIndex={0}
      role="button"
      onClick={() => onSelect(row.vendorId)}
      onKeyDown={(e) => {
        if (draft != null) return   // typing in the rename input, not navigating
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(row.vendorId) }
      }}
    >
      <td className="vendor-name">
        {draft == null ? (
          <>
            <span>{row.name}</span>
            <button
              className="icon-btn rename-btn"
              aria-label="Rename vendor"
              title="Rename vendor"
              onClick={(e) => { e.stopPropagation(); setDraft(row.name) }}
            >
              ✎
            </button>
          </>
        ) : (
          <input
            className="rename-input"
            aria-label="Vendor name"
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') commit()
              if (e.key === 'Escape') setDraft(null)
            }}
            onBlur={commit}
          />
        )}
      </td>
      <td className="cell verdict-cell">
        {verdict === 'idle' ? '—'
          : verdict === 'pending' ? <><span className="dot" /><span className="sr-only">pending</span></>
          : verdict === 'failed' ? <span className="failed">✗</span>
          : <span className={`pill ${bandForScore(verdict.score)}`}>
              {verdict.score}/10
            </span>}
      </td>
      {DIMENSIONS.map((d) => (
        <DimensionCell key={d.key} dim={d.key} state={row.cells[d.key] ?? 'idle'} />
      ))}
      <td className="cell actions-cell">
        <button
          className="delete-btn"
          aria-label="Delete vendor"
          title="Delete vendor"
          onClick={(e) => { e.stopPropagation(); onDelete(row.vendorId) }}
        >
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none"
               stroke="currentColor" strokeWidth="2" strokeLinecap="round"
               strokeLinejoin="round" aria-hidden="true">
            <path d="M3 6h18" />
            <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
            <path d="M10 11v6" />
            <path d="M14 11v6" />
          </svg>
        </button>
      </td>
    </tr>
  )
}
```

In `frontend/src/components/VendorTable.tsx`, add `onRename: (id: number, name: string) => void` to the props type and thread it to each `<VendorRow ... onRename={onRename} />`.

In `frontend/src/App.tsx`, add a reducer action + case:

```typescript
  | { kind: 'rename'; vendorId: number; name: string }
```

```typescript
    case 'rename':
      return state.map((r) =>
        r.vendorId === action.vendorId ? { ...r, name: action.name } : r)
```

add the handler next to `removeVendor`:

```typescript
  async function renameVendor(vendorId: number, name: string) {
    try {
      const v = await api.renameVendor(vendorId, name)
      dispatch({ kind: 'rename', vendorId, name: v.name })
    } catch (e) {
      // surfaces the API's 409 ("a vendor with that name is already in this project")
      setError(e instanceof Error ? e.message : 'Could not rename the vendor.')
    }
  }
```

and pass it through: `<VendorTable rows={sortedRows} onSelect={selectVendor} onDelete={removeVendor} onRename={renameVendor} />`.

In `frontend/src/styles.css`:

```css
.icon-btn { background: none; border: none; cursor: pointer; color: inherit; padding: 2px 4px; border-radius: 4px; }
.icon-btn:hover { background: var(--hover, rgba(128, 128, 128, 0.15)); }
.rename-btn { opacity: 0; font-size: 12px; }
.vendor-row:hover .rename-btn, .vendor-row:focus-within .rename-btn { opacity: 1; }
.rename-input { width: 100%; font: inherit; }
```

If `frontend/src/test/table.test.tsx` renders `<VendorTable>` or `<VendorRow>` directly, add `onRename={() => {}}` to keep it compiling.

- [ ] **Step 8: Run both suites and commit**

Run: `cd frontend && npx tsc -b && npx vitest run` and `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS

```bash
git add backend/src/vendor_dd/surfaces/api/store.py backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_store.py backend/tests/test_api.py frontend/src/api.ts frontend/src/App.tsx frontend/src/components/VendorRow.tsx frontend/src/components/VendorTable.tsx frontend/src/styles.css frontend/src/test/app.test.tsx
git commit -m "feat: rename a vendor without evicting its cached research

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Resume/retry button for partial, failed, and never-researched rows

A backend restart mid-research strands a row at e.g. 3/6 sections forever; a failed row's only documented recovery is delete + re-add (which evicts the good half of the cache). Add a ⟳ button that simply re-opens the report stream: the pipeline serves cached sections instantly and re-researches only what's missing, and the Task-4 single-flight registry makes double-clicks harmless. No backend changes.

**Files:**
- Modify: `frontend/src/components/VendorRow.tsx`, `frontend/src/components/VendorTable.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/test/app.test.tsx`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/test/app.test.tsx`:

```typescript
const partialVendor = {
  vendor_id: 9, name: 'Voith', vendor_key: 'voith.com', generated: true,
  sections_present: 2, sections_expected: 6, verdict_score: 5, verdict_reasoning: 'thin',
  dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
}

test('a partial report row offers resume, and clicking it re-opens the stream', async () => {
  mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [partialVendor] } },
  })
  render(<App />)
  await screen.findByText('Voith')

  await userEvent.click(screen.getByRole('button', { name: /resume research/i }))

  // a stream opened for that vendor and the row went back to streaming
  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  expect(es.url).toContain('/vendors/9/report/stream')
  expect(document.querySelector('.vendor-row .dot')).not.toBeNull()
})

test('a fully generated row does not offer resume', async () => {
  mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't',
      vendors: [{ ...partialVendor, sections_present: 6 }] } },
  })
  render(<App />)
  await screen.findByText('Voith')
  expect(screen.queryByRole('button', { name: /resume research/i })).toBeNull()
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/test/app.test.tsx`
Expected: the two new tests FAIL (no resume button exists)

- [ ] **Step 3: Implement**

In `frontend/src/components/VendorRow.tsx`:

1. Add `onResume: (id: number) => void` to the props type (and destructure it).
2. Add above the `return`:

```typescript
  // Resumable: research errored, never ran/never finished (idle), or a backend
  // restart left it partially cached. Re-opening the stream replays cached
  // sections instantly and re-researches only the missing ones.
  const resumable =
    row.status === 'error' ||
    row.status === 'idle' ||
    (row.status === 'done' && (row.sectionsPresent ?? 0) < (row.sectionsExpected ?? 0))
```

3. In the actions `<td>`, before the delete button:

```typescript
        {resumable && (
          <button
            className="icon-btn resume-btn"
            aria-label="Resume research"
            title="Resume research"
            onClick={(e) => { e.stopPropagation(); onResume(row.vendorId) }}
          >
            ⟳
          </button>
        )}
```

In `frontend/src/components/VendorTable.tsx`, add `onResume: (id: number) => void` to the props and thread it to `<VendorRow ... onResume={onResume} />`.

In `frontend/src/App.tsx`, add next to `removeVendor` (uses `startStreaming` — already imported — and `pollReport` from Task 6):

```typescript
  function resumeVendor(vendorId: number) {
    const row = rows.find((r) => r.vendorId === vendorId)
    if (!row || row.status === 'streaming') return
    streams.current.get(vendorId)?.()   // drop any stale poller/stream for this row
    dispatch({ kind: 'upsert', row: startStreaming(row) })
    const close = openReportStream(
      vendorId,
      (ev) => dispatch({ kind: 'event', vendorId, ev }),
      () => { streams.current.delete(vendorId); pollReport(vendorId) },
    )
    streams.current.set(vendorId, close)
  }
```

and pass it through: `<VendorTable ... onResume={resumeVendor} />`.

In `frontend/src/styles.css`, widen the actions column enough for two icons — find the actions-column width rule (48px, from the `table-layout: fixed` work) and change it to 72px.

If `frontend/src/test/table.test.tsx` renders `<VendorTable>`/`<VendorRow>` directly, add `onResume={() => {}}`.

- [ ] **Step 4: Run, build, commit**

Run: `cd frontend && npx tsc -b && npx vitest run`
Expected: all PASS

```bash
git add frontend/src/components/VendorRow.tsx frontend/src/components/VendorTable.tsx frontend/src/App.tsx frontend/src/styles.css frontend/src/test/app.test.tsx
git commit -m "feat: resume/retry button re-opens the report stream for partial or failed rows

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Badge for domain-level duplicates

Name-based dedup can't catch "Voith" vs "Voith Hydro" — both resolve to `voith.com` and silently share one cached report, showing as two identical rows. Surface it: the read model flags any vendor whose `vendor_key` is already used by an earlier vendor in the same project, and the row shows a small badge. Read-only — nothing is auto-deleted.

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/schemas.py` (VendorSummary)
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py` (get_project)
- Modify: `frontend/src/types.ts`, `frontend/src/rows.ts`, `frontend/src/components/VendorRow.tsx`, `frontend/src/styles.css`
- Test: `backend/tests/test_api.py`, `frontend/src/test/app.test.tsx`

- [ ] **Step 1: Write the failing backend test**

Append to `backend/tests/test_api.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_api.py::test_project_detail_flags_domain_level_duplicates -q`
Expected: FAIL — `KeyError: 'duplicate_of'`

- [ ] **Step 3: Implement the read-model flag**

In `backend/src/vendor_dd/surfaces/api/schemas.py`, add to `VendorSummary`:

```python
    duplicate_of: str | None = None   # earlier same-project vendor resolving to the same domain
```

In `backend/src/vendor_dd/surfaces/api/routes.py`, in `get_project`, after building `vendors` (list_vendors returns rows ordered by id, so the first holder of a key is the oldest):

```python
    seen_keys: dict[str, str] = {}
    for summ in vendors:
        if not summ.vendor_key:
            continue
        if summ.vendor_key in seen_keys:
            summ.duplicate_of = seen_keys[summ.vendor_key]
        else:
            seen_keys[summ.vendor_key] = summ.name
```

Run: `cd backend && .venv/bin/python -m pytest tests/ -q` — expect all PASS.

- [ ] **Step 4: Write the failing frontend test**

Append to `frontend/src/test/app.test.tsx`:

```typescript
test('a domain-level duplicate row shows a badge naming the canonical vendor', async () => {
  mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [
      { ...partialVendor, sections_present: 6, duplicate_of: null },
      { ...partialVendor, vendor_id: 10, name: 'Voith Hydro', sections_present: 6, duplicate_of: 'Voith' },
    ] } },
  })
  render(<App />)
  await screen.findByText('Voith Hydro')
  const badge = screen.getByTitle(/same company as Voith/i)
  expect(badge).toBeInTheDocument()
})
```

Run: `cd frontend && npx vitest run src/test/app.test.tsx` — expect FAIL.

- [ ] **Step 5: Implement types + row badge**

In `frontend/src/types.ts`, add to `VendorSummary`:

```typescript
  duplicate_of?: string | null
```

In `frontend/src/rows.ts`, add to `RowState`:

```typescript
  duplicateOf?: string | null
```

and in `rowFromSummary`, add to the `base` object:

```typescript
    duplicateOf: v.duplicate_of ?? null,
```

In `frontend/src/components/VendorRow.tsx`, in the non-editing branch of the name cell, after `<span>{row.name}</span>`:

```typescript
            {row.duplicateOf && (
              <span
                className="dup-badge"
                title={`Same company as ${row.duplicateOf} — the two rows share one research report`}
              >
                ≡
              </span>
            )}
```

In `frontend/src/styles.css`:

```css
.dup-badge { margin-left: 6px; font-size: 11px; opacity: 0.6; cursor: help; }
```

- [ ] **Step 6: Run both suites and commit**

Run: `cd frontend && npx tsc -b && npx vitest run` and `cd backend && .venv/bin/python -m pytest tests/ -q`
Expected: all PASS

```bash
git add backend/src/vendor_dd/surfaces/api/schemas.py backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_api.py frontend/src/types.ts frontend/src/rows.ts frontend/src/components/VendorRow.tsx frontend/src/styles.css frontend/src/test/app.test.tsx
git commit -m "feat: flag domain-level duplicate vendors in the read model and table

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Full verification + backend restart

- [ ] **Step 1: Run both suites**

Run: `cd backend && .venv/bin/python -m pytest tests/ -q` then `cd frontend && npx tsc -b && npx vitest run`
Expected: all PASS

- [ ] **Step 2: Restart the backend without erasing the cache**

```bash
kill -9 $(lsof -ti :8000) 2>/dev/null; sleep 2
cd backend && nohup .venv/bin/python -m uvicorn vendor_dd.surfaces.api.app:build_app --factory --host 127.0.0.1 --port 8000 > /tmp/vendor-dd-backend.log 2>&1 & disown
sleep 4 && lsof -ti :8000
```

Expected: a new pid listening on 8000. Do NOT delete `.vendor_dd_cache.db`.

Note: the existing dev DB may already contain duplicate vendor names from before Task 1 — the index migration tolerates that (it catches `IntegrityError` and keeps serving). If you want the constraint active on the dev DB, dedupe the rows manually first.

- [ ] **Step 3: Smoke-check in the browser**

- Add a vendor, refresh the page mid-research (stream drops) → the row keeps its blinking dots and eventually fills in; no "delete and re-add" banner.
- Rename a vendor with a typo → name updates, report still opens with its entity card.
- Hover a project in the sidebar → trash icon appears; delete a throwaway project → confirm dialog, project disappears, another project takes over.
- Find (or create) a partial row → ⟳ appears; click it → cached sections fill instantly, missing ones research.
