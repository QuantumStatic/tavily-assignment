# Portfolio Overview Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A global cross-project "Overview" dashboard that turns cached research into decisions (shortlist / red flags / most-trusted) plus calibration charts, powered by a read-only `/stats` endpoint, a "mark as chosen" flag, and an append-only score-history table.

**Architecture:** Two lanes with zero file overlap — **backend** (`backend/`) and **frontend** (`frontend/`) — run as concurrent implementer subagents in separate worktrees, merged before the integration task. Both build against the API contract fixed in the spec; the frontend tests against typed mocks of it (as existing frontend tests already mock `api`). All dashboard math is a pure, unit-tested function; the route only gathers inputs. The dashboard is strictly read-only over existing caches — no new generation behavior.

**Tech Stack:** Backend: FastAPI + `sqlite3` + Pydantic + pytest. Frontend: React + Vite + TypeScript + Vitest + Testing Library. Charts are plain CSS bars (no chart library).

**Spec:** `docs/superpowers/specs/2026-07-11-portfolio-dashboard-design.md`
**Mockup:** `scratchpad/dashboard-mockup.html` (reference for markup/classes)

---

## File Structure

**Backend (new):**
- `backend/src/vendor_dd/engine/history.py` — `ScoreHistory` (table + `record_score` + `chosen_counts`/`project_counts` reads). Owns its own connection like `SQLiteCache`.
- `backend/src/vendor_dd/surfaces/api/dashboard.py` — pure `compute_dashboard(...)` + the `DashboardStats` and nested response models.
- `backend/tests/test_history.py`, `backend/tests/test_dashboard.py` — new test files.

**Backend (modified):**
- `store.py` — `chosen` column; `set_chosen`; `list_all_vendors`; `chosen`/`vendor_key` reads for trust.
- `pipeline.py` — accept a `ScoreHistory | None`, record fresh section scores + verdict.
- `routes.py` — `PATCH /vendors/{id}/chosen`; `GET /stats`; report-endpoint trust+delta extensions; pass history into the engine.
- `app.py` — construct `ScoreHistory`, store on `app.state`.
- `schemas.py` (api) — `chosen` on `VendorOut`; trust/delta fields on `VendorReport`.

**Frontend (new):**
- `frontend/src/filter.ts` — `filterByName(items, query)` shared matcher.
- `frontend/src/components/ProjectFilter.tsx` — the dropdown.
- `frontend/src/components/Dashboard.tsx` — the Overview view + chart subcomponents.
- `frontend/src/dashboard.ts` — `DashboardStats` TS types (mirror of backend contract).
- Test files alongside: `filter.test.ts`, `projectFilter.test.tsx`, `dashboard.test.tsx`.

**Frontend (modified):**
- `Sidebar.tsx` — use `filterByName`; add "Overview" entry.
- `App.tsx` — render `<Dashboard/>` when `activeId===null`; drop first-project auto-select; chosen handler.
- `VendorTable.tsx` / `VendorRow.tsx` — chosen toggle; `onChosen` prop.
- `rows.ts` — `chosen` on `RowState`; carry through `rowFromSummary`.
- `ReportPanel.tsx` — trust line + per-dimension deltas.
- `types.ts` — `chosen` on `VendorSummary`/`VendorOut`; trust/delta fields on `VendorReport`.
- `api.ts` — `getStats`, `setChosen`.
- `styles.css` — dashboard + dropdown + chosen-toggle styles.

---

# LANE A — BACKEND

Runs in its own worktree. Tasks A1→A6 are sequential (shared `routes.py`/`store.py`). Run backend tests with `cd backend && python -m pytest`.

---

### Task A1: Score-history store

**Files:**
- Create: `backend/src/vendor_dd/engine/history.py`
- Test: `backend/tests/test_history.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_history.py
from datetime import date

import pytest

from vendor_dd.engine.history import ScoreHistory


def _hist(tmp_path, today=date(2026, 7, 11)):
    return ScoreHistory(tmp_path / "db.sqlite", clock=lambda: today)


def test_records_and_reads_back_a_dimension_score(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "legal", 8)
    assert h.scores_for("acme.com") == {"legal": (8, "2026-07-11")}


def test_same_day_rerun_replaces_the_row(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "legal", 8)
    h.record("acme.com", "legal", 4)   # same (key, dim, day) -> REPLACE
    assert h.scores_for("acme.com")["legal"] == (4, "2026-07-11")


def test_distinct_days_are_separate_rows(tmp_path):
    early = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 5, 1))
    early.record("acme.com", "legal", 8)
    late = ScoreHistory(tmp_path / "db.sqlite", clock=lambda: date(2026, 7, 11))
    late.record("acme.com", "legal", 4)
    # scores_for returns the LATEST row per dimension...
    assert late.scores_for("acme.com")["legal"] == (4, "2026-07-11")
    # ...and previous() returns the prior distinct-day score
    assert late.previous("acme.com", "legal") == (8, "2026-05-01")


def test_no_previous_when_only_one_day(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "legal", 8)
    assert h.previous("acme.com", "legal") is None


def test_verdict_is_a_valid_dimension(tmp_path):
    h = _hist(tmp_path)
    h.record("acme.com", "verdict", 6)
    assert h.scores_for("acme.com")["verdict"] == (6, "2026-07-11")


def test_rejects_unknown_dimension(tmp_path):
    h = _hist(tmp_path)
    with pytest.raises(Exception):
        h.record("acme.com", "bogus", 5)


def test_rejects_out_of_range_score(tmp_path):
    h = _hist(tmp_path)
    with pytest.raises(Exception):
        h.record("acme.com", "legal", 11)


def test_snapshot_is_not_a_valid_history_dimension(tmp_path):
    h = _hist(tmp_path)
    with pytest.raises(Exception):
        h.record("acme.com", "snapshot", 5)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_history.py -q`
Expected: FAIL — `ModuleNotFoundError: vendor_dd.engine.history`

- [ ] **Step 3: Implement**

```python
# backend/src/vendor_dd/engine/history.py
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Callable

from vendor_dd.engine.schemas import Dimension
from vendor_dd.logs import get_logger

_LOG = get_logger("db")


def _today() -> date:
    # only used as a default; production passes the pipeline's injected date
    return date.today()


# Valid history dimensions: every scored report Dimension (i.e. all except SNAPSHOT,
# which is entity resolution not a score) PLUS the synthesized 'verdict'. Generated
# from the enum so the CHECK constraint has a single source of truth. VERDICT is
# deliberately NOT a Dimension member — code iterates that enum where verdict must
# not appear (EXPECTED_SECTIONS, _TAVILY_DIMS).
HISTORY_DIMENSIONS: tuple[str, ...] = tuple(
    d.value for d in Dimension if d is not Dimension.SNAPSHOT
) + ("verdict",)

_CHECK_LIST = ", ".join(f"'{d}'" for d in HISTORY_DIMENSIONS)


class ScoreHistory:
    """Append-only institutional memory of every score ever assigned, keyed by resolved
    domain (vendor_key). Survives vendor deletion and cache eviction deliberately.
    One row per (vendor_key, dimension, day); same-day re-runs REPLACE (last write wins)."""

    def __init__(self, path: str | Path, clock: Callable[[], date] = _today):
        self._clock = clock
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            f"""CREATE TABLE IF NOT EXISTS score_history (
                  vendor_key  TEXT NOT NULL,
                  dimension   TEXT NOT NULL CHECK (dimension IN ({_CHECK_LIST})),
                  score       INTEGER NOT NULL CHECK (score BETWEEN 0 AND 10),
                  recorded_on TEXT NOT NULL,
                  PRIMARY KEY (vendor_key, dimension, recorded_on)
                )"""
        )
        self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()):
        cur = self._conn.execute(sql, params)
        _LOG.info("db.query", extra={"payload": {
            "sql": " ".join(sql.split()), "params": list(params),
            "rowcount": cur.rowcount, "lastrowid": cur.lastrowid,
        }})
        return cur

    def record(self, vendor_key: str, dimension: str, score: int) -> None:
        self._exec(
            """INSERT OR REPLACE INTO score_history (vendor_key, dimension, score, recorded_on)
               VALUES (?,?,?,?)""",
            (vendor_key, dimension, score, self._clock().isoformat()),
        )
        self._conn.commit()

    def scores_for(self, vendor_key: str) -> dict[str, tuple[int, str]]:
        """Latest (score, recorded_on) per dimension for this vendor_key."""
        cur = self._exec(
            """SELECT dimension, score, recorded_on FROM score_history
               WHERE vendor_key=? ORDER BY dimension, recorded_on""",
            (vendor_key,),
        )
        out: dict[str, tuple[int, str]] = {}
        for dim, score, recorded_on in cur.fetchall():
            out[dim] = (score, recorded_on)   # ORDER BY recorded_on => last wins = latest
        return out

    def previous(self, vendor_key: str, dimension: str) -> tuple[int, str] | None:
        """The score recorded on the second-most-recent distinct day, or None."""
        cur = self._exec(
            """SELECT score, recorded_on FROM score_history
               WHERE vendor_key=? AND dimension=?
               ORDER BY recorded_on DESC LIMIT 2""",
            (vendor_key, dimension),
        )
        rows = cur.fetchall()
        if len(rows) < 2:
            return None
        score, recorded_on = rows[1]
        return score, recorded_on

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_history.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/history.py backend/tests/test_history.py
git commit -m "feat: append-only score-history store keyed by domain"
```

---

### Task A2: Record scores from the pipeline

**Files:**
- Modify: `backend/src/vendor_dd/engine/pipeline.py`
- Test: `backend/tests/test_pipeline_history.py` (new)

The engine gains an optional `history: ScoreHistory | None`. It records the score of every **freshly computed** section (not cache hits — their score is from an earlier day and already in history) and the verdict at completion. History write failures must never fail generation.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_pipeline_history.py
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


def test_history_is_optional(tmp_path):
    # no history passed -> generation still works (CLI/unit path)
    deps = _deps(tmp_path)
    events = list(ReportEngine(deps, mode="sequential").iter_events("Acme"))
    assert any(type(e).__name__ == "ReportComplete" for e in events)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_pipeline_history.py -q`
Expected: FAIL — `ReportEngine.__init__ got an unexpected keyword argument 'history'`

- [ ] **Step 3: Implement**

In `pipeline.py`, add the import near the other engine imports:

```python
from vendor_dd.engine.history import ScoreHistory
```

Extend the constructor signature and store it (add `history` param after `domain_locks`):

```python
    def __init__(self, deps: Deps, *, mode: Literal["parallel", "sequential"] = "parallel",
                 max_workers: int = 6, session_id: str | None = None,
                 domain_locks: KeyedLocks | None = None,
                 history: ScoreHistory | None = None):
        self._deps = deps
        self._max_workers = 1 if mode == "sequential" else max_workers
        self._search: SearchClient = (
            _SessionSearch(deps.search, session_id) if session_id else deps.search)
        self._domain_locks = domain_locks
        self._history = history
```

Add a private helper (best-effort, never raises) after `_domain_guard`:

```python
    def _record_score(self, vendor_key: str, dimension: str, score: int) -> None:
        """Best-effort: history is institutional memory, not the product. A failed write
        must never fail generation."""
        if self._history is None:
            return
        try:
            self._history.record(vendor_key, dimension, score)
        except Exception as exc:   # noqa: BLE001 - deliberately swallow
            _LOG.error("history.error", extra={"payload": {
                "vendor_key": vendor_key, "dimension": dimension, "error": str(exc)}})
```

In `iter_events`, in the fan-out completion loop, right after the existing
`cache.put(vendor_key, dim, outcome.section...)` (currently ~line 145) add:

```python
                        self._record_score(vendor_key, dim.value, outcome.section.score)
```

For backlog: after `sections.append(backlog)` and only when freshly computed, record it. Replace the backlog append block:

```python
                sections.append(backlog)
                if not backlog_cached:
                    self._record_score(vendor_key, Dimension.BACKLOG.value, backlog.score)
                yield SectionComplete(section=backlog, cached=backlog_cached)
```

After the verdict is computed successfully (right after `report = Report(...)`), add:

```python
                self._record_score(vendor_key, "verdict", score)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_pipeline_history.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/pipeline.py backend/tests/test_pipeline_history.py
git commit -m "feat: record fresh section + verdict scores to history during generation"
```

---

### Task A3: `chosen` column + `PATCH /vendors/{id}/chosen`

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/store.py`, `routes.py`, `schemas.py`
- Test: `backend/tests/test_store.py` (append), `backend/tests/test_api.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_store.py`:

```python
def test_new_vendor_defaults_to_not_chosen(tmp_path):
    from vendor_dd.surfaces.api.store import Store
    store = Store(tmp_path / "db.sqlite")
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Acme")
    assert store.get_vendor(v.id).chosen is False


def test_set_chosen_toggles_the_flag(tmp_path):
    from vendor_dd.surfaces.api.store import Store
    store = Store(tmp_path / "db.sqlite")
    p = store.create_project("p")
    v = store.add_vendor(p.id, "Acme")
    store.set_chosen(v.id, True)
    assert store.get_vendor(v.id).chosen is True
    store.set_chosen(v.id, False)
    assert store.get_vendor(v.id).chosen is False


def test_list_all_vendors_spans_projects(tmp_path):
    from vendor_dd.surfaces.api.store import Store
    store = Store(tmp_path / "db.sqlite")
    a = store.create_project("a"); b = store.create_project("b")
    store.add_vendor(a.id, "Acme"); store.add_vendor(b.id, "Beta")
    names = {v.name for v in store.list_all_vendors()}
    assert names == {"Acme", "Beta"}
```

Append to `backend/tests/test_api.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_store.py tests/test_api.py -q -k "chosen or list_all"`
Expected: FAIL — `Vendor` has no `chosen`; route 404s as not-found.

- [ ] **Step 3: Implement**

In `store.py`, add `chosen: bool` to the `Vendor` dataclass (default `False`):

```python
@dataclass
class Vendor:
    id: int
    project_id: int
    name: str
    vendor_key: str | None
    created_at: str
    chosen: bool = False
```

In `Store.__init__`, after the `vendors` CREATE TABLE, add the column defensively (the DB is wiped, but this keeps a fresh file self-consistent — one statement, no migration framework):

```python
        # `chosen` marks a vendor the user actually hired — powers cross-project trust stats.
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(vendors)").fetchall()]
        if "chosen" not in cols:
            self._exec("ALTER TABLE vendors ADD COLUMN chosen INTEGER NOT NULL DEFAULT 0")
```

Update every vendor SELECT to include `chosen` and map it to `bool`. Add a small row mapper near the top of the class and use it in `find_vendor`, `get_vendor`, `list_vendors`, plus the new `list_all_vendors`. Concretely, change the three existing `SELECT id, project_id, name, vendor_key, created_at ...` reads to `SELECT id, project_id, name, vendor_key, created_at, chosen ...` and construct with a mapper:

```python
    @staticmethod
    def _vendor(row) -> Vendor:
        id_, project_id, name, vendor_key, created_at, chosen = row
        return Vendor(id=id_, project_id=project_id, name=name, vendor_key=vendor_key,
                      created_at=created_at, chosen=bool(chosen))
```

- `find_vendor`: `... created_at, chosen FROM vendors WHERE ...` then `return Store._vendor(row) if row else None`
- `get_vendor`: same column list, `return Store._vendor(row) if row else None`
- `list_vendors`: same column list, `return [Store._vendor(r) for r in cur.fetchall()]`
- `add_vendor`: the returned `Vendor(...)` already omits chosen (defaults False) — leave as is.

Add two new methods:

```python
    def list_all_vendors(self) -> list[Vendor]:
        cur = self._exec(
            "SELECT id, project_id, name, vendor_key, created_at, chosen FROM vendors ORDER BY id")
        return [Store._vendor(r) for r in cur.fetchall()]

    def set_chosen(self, vendor_id: int, chosen: bool) -> Vendor | None:
        if self.get_vendor(vendor_id) is None:
            return None
        self._exec("UPDATE vendors SET chosen=? WHERE id=?", (1 if chosen else 0, vendor_id))
        self._conn.commit()
        return self.get_vendor(vendor_id)
```

In `schemas.py` (api), add `chosen` to `VendorOut` and `VendorSummary`, and a `ChosenIn` body model:

```python
class ChosenIn(BaseModel):
    chosen: bool
```

Add `chosen: bool = False` to `VendorOut` and `chosen: bool = False` to `VendorSummary`.

In `routes.py`, update `_vendor_out` to pass chosen, and `_summarize` to set it. Change `_vendor_out`:

```python
def _vendor_out(v: Vendor, *, existed: bool) -> VendorOut:
    return VendorOut(id=v.id, project_id=v.project_id, name=v.name, vendor_key=v.vendor_key,
                     created_at=v.created_at, existed=existed, chosen=v.chosen)
```

In `_summarize`, add `chosen=vendor.chosen` to BOTH `VendorSummary(...)` return sites.

Add the route (import `ChosenIn` in the schemas import block):

```python
@router.patch("/vendors/{vendor_id}/chosen", response_model=VendorOut)
def set_chosen(vendor_id: int, body: ChosenIn, request: Request):
    v = _store(request).set_chosen(vendor_id, body.chosen)
    if v is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    return _vendor_out(v, existed=True)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_store.py tests/test_api.py -q`
Expected: PASS (all, including the two new API tests). Existing tests still green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/store.py backend/src/vendor_dd/surfaces/api/routes.py backend/src/vendor_dd/surfaces/api/schemas.py backend/tests/test_store.py backend/tests/test_api.py
git commit -m "feat: mark-as-chosen flag on vendors + PATCH /vendors/{id}/chosen"
```

---

### Task A4: Pure `compute_dashboard` + response models

**Files:**
- Create: `backend/src/vendor_dd/surfaces/api/dashboard.py`
- Test: `backend/tests/test_dashboard.py`

`compute_dashboard` is a pure function over plain inputs — no DB. Inputs:
- `vendors`: list of `VendorRow` (id, name, project_id, project_name, vendor_key, chosen, verdict, dims) — one per vendor row in the SELECTED projects. `verdict` is `int | None` (None = not generated); `dims` is `dict[str,int]` of scored dimensions (empty if not generated).
- `previous_verdict`: `dict[str, int]` mapping vendor_key → prior-day verdict (for shortlist deltas).
- `chosen_counts` / `project_counts`: `dict[str, int]` by vendor_key across ALL projects.
- `projects_total`, `projects_selected`: ints.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_dashboard.py
from vendor_dd.surfaces.api.dashboard import VendorRow, compute_dashboard
from vendor_dd.engine.schemas import Dimension


def _row(id, name, pid, pname, key, verdict, dims, chosen=False):
    return VendorRow(vendor_id=id, name=name, project_id=pid, project_name=pname,
                     vendor_key=key, chosen=chosen, verdict=verdict, dims=dims)


FULL = {"legal": 7, "safety": 4, "financial": 6, "backlog": 8, "certifications": 9, "news": 5}


def test_counts_and_coverage():
    rows = [_row(1, "A", 1, "P", "a.com", 8, FULL),
            _row(2, "B", 1, "P", "b.com", None, {})]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.vendors_total == 2 and d.vendors_generated == 1


def test_avg_verdict_only_over_generated():
    rows = [_row(1, "A", 1, "P", "a.com", 8, FULL),
            _row(2, "B", 1, "P", "b.com", 4, FULL),
            _row(3, "C", 1, "P", "c.com", None, {})]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.avg_verdict == 6.0   # (8+4)/2, the None ignored


def test_risk_bands_boundaries():
    rows = [_row(1, "A", 1, "P", "a.com", 3, FULL),   # high (<=3)
            _row(2, "B", 1, "P", "b.com", 4, FULL),   # watch
            _row(3, "C", 1, "P", "c.com", 6, FULL),   # watch
            _row(4, "D", 1, "P", "d.com", 7, FULL)]   # cleared (>=7)
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert (d.risk_high, d.risk_watch, d.risk_cleared) == (1, 2, 1)


def test_verdict_histogram_indexes_by_score():
    rows = [_row(1, "A", 1, "P", "a.com", 7, FULL),
            _row(2, "B", 1, "P", "b.com", 7, FULL),
            _row(3, "C", 1, "P", "c.com", 2, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert len(d.verdict_histogram) == 11
    assert d.verdict_histogram[7] == 2 and d.verdict_histogram[2] == 1


def test_weakest_dimension_is_lowest_average():
    rows = [_row(1, "A", 1, "P", "a.com", 6, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.weakest_dimension == Dimension.SAFETY   # 4 is the min in FULL


def test_weakest_dimension_ties_break_by_enum_order():
    dims = {"legal": 4, "safety": 4, "financial": 9, "backlog": 9,
            "certifications": 9, "news": 9}
    rows = [_row(1, "A", 1, "P", "a.com", 6, dims)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    assert d.weakest_dimension == Dimension.LEGAL   # legal precedes safety in enum order


def test_duplicate_domain_counts_once_for_domain_stats():
    rows = [_row(1, "Voith", 1, "P", "voith.com", 8, FULL),
            _row(2, "Voith Hydro", 2, "Q", "voith.com", 8, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=2, projects_selected=2)
    # domain-level: one entry in the histogram, but vendors_total counts both rows
    assert d.vendors_total == 2
    assert sum(d.verdict_histogram) == 1


def test_red_flag_low_dimension_named():
    rows = [_row(1, "Fluor", 1, "P", "fluor.com", 6,
                 {**FULL, "safety": 2})]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1)
    flags = [f for f in d.red_flags if f.kind == "dimension"]
    assert any(f.ref.name == "Fluor" and f.dimension == Dimension.SAFETY and f.score == 2
               for f in flags)


def test_delivery_risk_combo_triggers_only_when_both_weak():
    weak = _row(1, "Aecon", 1, "P", "aecon.com", 5,
                {**FULL, "backlog": 4, "financial": 3})
    ok = _row(2, "Bech", 1, "P", "bech.com", 6, {**FULL, "backlog": 4, "financial": 5})
    d = compute_dashboard([weak, ok], {}, {}, {}, projects_total=1, projects_selected=1)
    combos = [f for f in d.red_flags if f.kind == "delivery_risk"]
    assert [f.ref.name for f in combos] == ["Aecon"]


def test_shortlist_top_three_by_verdict_with_delta():
    rows = [_row(i, f"V{i}", 1, "P", f"v{i}.com", v, FULL)
            for i, v in [(1, 9), (2, 8), (3, 7), (4, 6)]]
    d = compute_dashboard(rows, {"v1.com": 6}, {}, {}, projects_total=1, projects_selected=1)
    assert [e.ref.name for e in d.shortlist] == ["V1", "V2", "V3"]
    assert d.shortlist[0].previous_verdict == 6


def test_most_trusted_ranks_by_chosen_count():
    rows = [_row(1, "A", 1, "P", "a.com", 7, FULL),
            _row(2, "B", 1, "P", "b.com", 9, FULL)]
    d = compute_dashboard(rows, {}, {"a.com": 4, "b.com": 1}, {"a.com": 5, "b.com": 2},
                          projects_total=1, projects_selected=1)
    assert d.most_trusted[0].name == "A" and d.most_trusted[0].chosen_count == 4
    assert d.most_trusted[0].project_count == 5


def test_source_counts_passed_through():
    rows = [_row(1, "A", 1, "P", "a.com", 7, FULL)]
    d = compute_dashboard(rows, {}, {}, {}, projects_total=1, projects_selected=1,
                          independent_sources=8, self_reported_sources=2)
    assert (d.independent_sources, d.self_reported_sources) == (8, 2)


def test_empty_selection_is_all_zeros_no_crash():
    d = compute_dashboard([], {}, {}, {}, projects_total=3, projects_selected=0)
    assert d.avg_verdict is None and d.weakest_dimension is None
    assert d.risk_high == 0 and d.shortlist == [] and sum(d.verdict_histogram) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_dashboard.py -q`
Expected: FAIL — `ModuleNotFoundError: ...api.dashboard`

- [ ] **Step 3: Implement**

```python
# backend/src/vendor_dd/surfaces/api/dashboard.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from vendor_dd.engine.schemas import Dimension

# scored dimensions in enum order (excludes SNAPSHOT); ties in "weakest" break by this order
SCORED_DIMS: list[Dimension] = [d for d in Dimension if d is not Dimension.SNAPSHOT]

HIGH_MAX = 3      # verdict <= 3 -> high risk
CLEARED_MIN = 7   # verdict >= 7 -> cleared
RED_FLAG_MAX = 3  # any dimension <= 3 -> named red flag
DELIVERY_MAX = 4  # backlog <= 4 AND financial <= 4 -> delivery-risk combo


@dataclass
class VendorRow:
    vendor_id: int
    name: str
    project_id: int
    project_name: str
    vendor_key: str | None
    chosen: bool
    verdict: int | None
    dims: dict[str, int]


class VendorRef(BaseModel):
    vendor_id: int
    name: str
    project_id: int
    project_name: str


class ShortlistEntry(BaseModel):
    ref: VendorRef
    verdict: int
    previous_verdict: int | None = None


class RedFlag(BaseModel):
    ref: VendorRef
    kind: Literal["dimension", "delivery_risk"]
    dimension: Dimension | None = None
    score: int
    detail: str


class TrustedVendor(BaseModel):
    name: str
    vendor_key: str
    chosen_count: int
    project_count: int
    verdict: int | None = None


class DashboardStats(BaseModel):
    projects_total: int
    projects_selected: int
    vendors_total: int
    vendors_generated: int
    avg_verdict: float | None
    risk_high: int
    risk_watch: int
    risk_cleared: int
    independent_sources: int
    self_reported_sources: int
    verdict_histogram: list[int]
    dimension_avgs: dict[Dimension, float]
    dimension_histograms: dict[Dimension, list[int]]
    weakest_dimension: Dimension | None
    weakest_low_count: int
    shortlist: list[ShortlistEntry]
    red_flags: list[RedFlag]
    most_trusted: list[TrustedVendor]


def _ref(v: VendorRow) -> VendorRef:
    return VendorRef(vendor_id=v.vendor_id, name=v.name,
                     project_id=v.project_id, project_name=v.project_name)


def compute_dashboard(
    vendors: list[VendorRow],
    previous_verdict: dict[str, int],
    chosen_counts: dict[str, int],
    project_counts: dict[str, int],
    *,
    projects_total: int,
    projects_selected: int,
    independent_sources: int = 0,
    self_reported_sources: int = 0,
) -> DashboardStats:
    # Domain-level dedupe: collapse same vendor_key to the earliest (first-seen) row for
    # all domain stats; row-level counts (vendors_total) still count every row.
    canonical: dict[str, VendorRow] = {}
    for v in vendors:
        key = v.vendor_key
        if key and key not in canonical:
            canonical[key] = v
    unique = list(canonical.values())
    generated = [v for v in unique if v.verdict is not None]

    verdicts = [v.verdict for v in generated]
    avg_verdict = round(sum(verdicts) / len(verdicts), 1) if verdicts else None

    risk_high = sum(1 for s in verdicts if s <= HIGH_MAX)
    risk_cleared = sum(1 for s in verdicts if s >= CLEARED_MIN)
    risk_watch = len(verdicts) - risk_high - risk_cleared

    verdict_histogram = [0] * 11
    for s in verdicts:
        verdict_histogram[s] += 1

    dimension_avgs: dict[Dimension, float] = {}
    dimension_histograms: dict[Dimension, list[int]] = {}
    for dim in SCORED_DIMS:
        vals = [v.dims[dim.value] for v in generated if dim.value in v.dims]
        hist = [0] * 11
        for s in vals:
            hist[s] += 1
        dimension_histograms[dim] = hist
        if vals:
            dimension_avgs[dim] = round(sum(vals) / len(vals), 1)

    weakest_dimension = None
    weakest_low_count = 0
    if dimension_avgs:
        # min average; ties broken by SCORED_DIMS order (min() is stable over the ordered list)
        weakest_dimension = min(
            (d for d in SCORED_DIMS if d in dimension_avgs),
            key=lambda d: dimension_avgs[d])
        weakest_low_count = sum(
            1 for v in generated
            if v.dims.get(weakest_dimension.value, 99) <= RED_FLAG_MAX)

    shortlist = [
        ShortlistEntry(ref=_ref(v), verdict=v.verdict,
                       previous_verdict=previous_verdict.get(v.vendor_key or ""))
        for v in sorted(generated, key=lambda v: v.verdict, reverse=True)[:3]
    ]

    red_flags: list[RedFlag] = []
    for v in generated:
        low = [(d, v.dims[d.value]) for d in SCORED_DIMS
               if v.dims.get(d.value, 99) <= RED_FLAG_MAX]
        for dim, score in low:
            red_flags.append(RedFlag(ref=_ref(v), kind="dimension", dimension=dim,
                                     score=score, detail=f"{dim.value.title()} {score}"))
        bl, fin = v.dims.get("backlog"), v.dims.get("financial")
        if bl is not None and fin is not None and bl <= DELIVERY_MAX and fin <= DELIVERY_MAX:
            red_flags.append(RedFlag(ref=_ref(v), kind="delivery_risk", dimension=None,
                                     score=min(bl, fin),
                                     detail=f"Backlog {bl} · Financial {fin}"))

    trusted = [
        TrustedVendor(name=v.name, vendor_key=v.vendor_key or "",
                      chosen_count=chosen_counts.get(v.vendor_key or "", 0),
                      project_count=project_counts.get(v.vendor_key or "", 0),
                      verdict=v.verdict)
        for v in unique if v.vendor_key
    ]
    trusted = [t for t in trusted if t.chosen_count > 0]
    trusted.sort(key=lambda t: (t.chosen_count, t.verdict or 0), reverse=True)

    return DashboardStats(
        projects_total=projects_total, projects_selected=projects_selected,
        vendors_total=len(vendors), vendors_generated=len(generated),
        avg_verdict=avg_verdict,
        risk_high=risk_high, risk_watch=risk_watch, risk_cleared=risk_cleared,
        independent_sources=independent_sources, self_reported_sources=self_reported_sources,
        verdict_histogram=verdict_histogram,
        dimension_avgs=dimension_avgs, dimension_histograms=dimension_histograms,
        weakest_dimension=weakest_dimension, weakest_low_count=weakest_low_count,
        shortlist=shortlist, red_flags=red_flags, most_trusted=trusted[:3],
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_dashboard.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/dashboard.py backend/tests/test_dashboard.py
git commit -m "feat: pure compute_dashboard aggregation + response models"
```

---

### Task A5: `GET /stats` route

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py`, `app.py`
- Test: `backend/tests/test_api.py` (append)

Wires the store + cache + history into `compute_dashboard`. Source counts come from parsing each cached section's findings' citations.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py`:

```python
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
```

Note: `FakeLLM.structured` returns a `Section` with `findings=[]`, so source counts will be 0 — that's fine for these tests. (The dashboard test file already covers source-count math directly.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_api.py -q -k stats`
Expected: FAIL — 404 (no `/stats` route).

- [ ] **Step 3: Implement**

In `app.py`, construct the history store next to the others:

```python
    from vendor_dd.engine.history import ScoreHistory
    app.state.history = ScoreHistory(deps.cache_path)
```

In `routes.py`, add imports:

```python
from vendor_dd.engine.schemas import Dimension, EntityCard, Section, SourceType
from vendor_dd.surfaces.api.dashboard import DashboardStats, VendorRow, compute_dashboard
```

Add a helper to parse the projects query param and the source counts, then the route. Put near the other helpers:

```python
def _parse_project_ids(projects: str | None) -> set[int] | None:
    """None -> all projects. Else the set of valid ints; unknown ids filtered later.
    Raises 422 on non-integer tokens."""
    if projects is None or projects.strip() == "":
        return None
    try:
        return {int(tok) for tok in projects.split(",") if tok.strip() != ""}
    except ValueError:
        raise HTTPException(status_code=422, detail="projects must be comma-separated integers")


def _count_sources(sections: dict) -> tuple[int, int]:
    independent = self_reported = 0
    for sec, _ in sections.values():
        for f in sec.findings:
            if f.citation.source_type is SourceType.INDEPENDENT:
                independent += 1
            else:
                self_reported += 1
    return independent, self_reported
```

Add the route:

```python
@router.get("/stats", response_model=DashboardStats)
def get_stats(request: Request, projects: str | None = None):
    store = _store(request)
    history = request.app.state.history

    wanted = _parse_project_ids(projects)
    all_projects = store.list_projects()
    projects_total = len(all_projects)
    valid_ids = {p.id for p in all_projects}
    selected_ids = valid_ids if wanted is None else (wanted & valid_ids)
    name_by_id = {p.id: p.name for p in all_projects}

    cache = _cache(request)
    try:
        rows: list[VendorRow] = []
        independent = self_reported = 0
        previous_verdict: dict[str, int] = {}
        for v in store.list_all_vendors():
            if v.project_id not in selected_ids:
                continue
            dims: dict[str, int] = {}
            verdict: int | None = None
            if v.vendor_key:
                parsed, _snap = _report_sections(cache, v.vendor_key)
                if parsed:
                    dims = {d.value: sec.score for d, (sec, _ts) in parsed.items()}
                    score, _reasoning = assemble_verdict([sec for sec, _ in parsed.values()])
                    verdict = score
                    ind, self_r = _count_sources(parsed)
                    independent += ind
                    self_reported += self_r
                    prev = history.previous(v.vendor_key, "verdict")
                    if prev is not None:
                        previous_verdict[v.vendor_key] = prev[0]
            rows.append(VendorRow(
                vendor_id=v.id, name=v.name, project_id=v.project_id,
                project_name=name_by_id.get(v.project_id, ""), vendor_key=v.vendor_key,
                chosen=v.chosen, verdict=verdict, dims=dims))

        # trust counts span ALL projects, by domain
        chosen_counts: dict[str, int] = {}
        project_counts: dict[str, int] = {}
        for v in store.list_all_vendors():
            if not v.vendor_key:
                continue
            project_counts[v.vendor_key] = project_counts.get(v.vendor_key, 0) + 1
            if v.chosen:
                chosen_counts[v.vendor_key] = chosen_counts.get(v.vendor_key, 0) + 1
    finally:
        cache.close()

    return compute_dashboard(
        rows, previous_verdict, chosen_counts, project_counts,
        projects_total=projects_total, projects_selected=len(selected_ids),
        independent_sources=independent, self_reported_sources=self_reported)
```

Note `_report_sections` currently returns `(sections, snapshot)`; keep that call shape.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_api.py -q`
Expected: PASS (all, including the 5 new stats tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/routes.py backend/src/vendor_dd/surfaces/api/app.py backend/tests/test_api.py
git commit -m "feat: GET /stats read-only dashboard aggregation endpoint"
```

---

### Task A6: Report-endpoint trust + delta extensions

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py`, `schemas.py`
- Test: `backend/tests/test_api.py` (append)

`VendorReport` gains `chosen_count`, `projects_count`, and `dimension_deltas` (per-dimension prior score). Wire the stream route to pass `history` into the engine.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_api.py`:

```python
def test_report_carries_trust_counts(tmp_path):
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "p"}).json()["id"]
    vid = _generate(client, pid, "Cives Steel")
    client.patch(f"/vendors/{vid}/chosen", json={"chosen": True})
    r = client.get(f"/vendors/{vid}/report").json()
    assert r["chosen_count"] == 1 and r["projects_count"] == 1
    # deltas present as a dict (no prior day yet -> values may be null)
    assert "dimension_deltas" in r
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_api.py -q -k trust`
Expected: FAIL — `KeyError: chosen_count` (field absent).

- [ ] **Step 3: Implement**

In `schemas.py` (api), add to `VendorReport`:

```python
class DimensionDelta(BaseModel):
    score: int
    recorded_on: str

# in VendorReport:
    chosen_count: int = 0
    projects_count: int = 0
    dimension_deltas: dict[str, DimensionDelta | None] = {}
```

In `routes.py` `get_report`, after computing `entity` and before returning, gather trust + deltas. Add near the top of the function (after `vendor = store.get_vendor(...)`):

```python
    history = request.app.state.history
```

Replace the two `return VendorReport(...)` sites to include the new fields. For the not-generated early return, add `chosen_count=0, projects_count=0, dimension_deltas={}`. For the generated return, compute:

```python
        chosen_count = projects_count = 0
        deltas: dict[str, DimensionDelta | None] = {}
        if vendor.vendor_key:
            for other in store.list_all_vendors():
                if other.vendor_key == vendor.vendor_key:
                    projects_count += 1
                    if other.chosen:
                        chosen_count += 1
            for d, (sec, _ts) in parsed.items():
                prev = history.previous(vendor.vendor_key, d.value)
                deltas[d.value] = (DimensionDelta(score=prev[0], recorded_on=prev[1])
                                   if prev else None)
```

and pass `chosen_count=chosen_count, projects_count=projects_count, dimension_deltas=deltas` into the generated `VendorReport(...)`. Import `DimensionDelta` in the schemas import block.

In `stream_report`, pass history into the engine so generation records scores:

```python
        engine = ReportEngine(request.app.state.deps, mode="parallel", session_id=session_id,
                              domain_locks=request.app.state.domain_locks,
                              history=request.app.state.history)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_api.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit + full backend suite**

```bash
cd backend && python -m pytest -q
git add backend/src/vendor_dd/surfaces/api/routes.py backend/src/vendor_dd/surfaces/api/schemas.py backend/tests/test_api.py
git commit -m "feat: report endpoint carries cross-project trust counts + score deltas"
```

Expected: full backend suite green.

---

# LANE B — FRONTEND

Runs in its own worktree, concurrent with Lane A. Tasks B1→B5 sequential (several touch `App.tsx`). Run frontend tests with `cd frontend && npx vitest run`. Build against the mocked API contract; no backend needed until integration.

---

### Task B1: `filterByName` shared helper + Sidebar refactor

**Files:**
- Create: `frontend/src/filter.ts`, `frontend/src/test/filter.test.ts`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/test/filter.test.ts
import { expect, test } from 'vitest'
import { filterByName } from '../filter'

const items = [{ name: 'Bridge job' }, { name: 'HVAC Q3' }, { name: 'Turbines RFP' }]

test('empty query returns everything', () => {
  expect(filterByName(items, '')).toHaveLength(3)
  expect(filterByName(items, '   ')).toHaveLength(3)
})

test('matches case- and space-insensitively on substring', () => {
  expect(filterByName(items, 'hvac').map((i) => i.name)).toEqual(['HVAC Q3'])
  expect(filterByName(items, '  BRIDGE ').map((i) => i.name)).toEqual(['Bridge job'])
})

test('no match returns empty', () => {
  expect(filterByName(items, 'zzz')).toEqual([])
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/test/filter.test.ts`
Expected: FAIL — cannot resolve `../filter`.

- [ ] **Step 3: Implement**

```typescript
// frontend/src/filter.ts
/** Shared project/vendor name matcher: trim + lowercase + substring. Used by the
 *  sidebar search and the dashboard project filter so matching can't drift. */
export function filterByName<T extends { name: string }>(items: T[], query: string): T[] {
  const q = query.trim().toLowerCase()
  if (q === '') return items
  return items.filter((i) => i.name.toLowerCase().includes(q))
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/filter.test.ts`
Expected: PASS (3 tests)

- [ ] **Step 5: Refactor Sidebar to use it**

In `Sidebar.tsx`, replace the inline filter line:

```typescript
  const filtered = q ? projects.filter((p) => p.name.toLowerCase().includes(ql)) : projects
```

with (add `import { filterByName } from '../filter'` at top; `ql` may now be unused — remove it):

```typescript
  const filtered = filterByName(projects, q)
```

- [ ] **Step 6: Verify existing sidebar tests still pass**

Run: `cd frontend && npx vitest run src/test/sidebar.test.tsx src/test/filter.test.ts`
Expected: PASS (all existing sidebar tests + filter tests — behavior unchanged).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/filter.ts frontend/src/test/filter.test.ts frontend/src/components/Sidebar.tsx
git commit -m "refactor: extract shared filterByName matcher, use in Sidebar"
```

---

### Task B2: `ProjectFilter` dropdown

**Files:**
- Create: `frontend/src/components/ProjectFilter.tsx`, `frontend/src/test/projectFilter.test.tsx`
- Modify: `frontend/src/styles.css`

Props: `projects: {id, name, vendorCount}[]`, `selectedIds: Set<number>`, `onChange(ids: Set<number>)`.

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/src/test/projectFilter.test.tsx
import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProjectFilter } from '../components/ProjectFilter'

const projects = [
  { id: 1, name: 'Bridge job', vendorCount: 5 },
  { id: 2, name: 'HVAC Q3', vendorCount: 6 },
  { id: 3, name: 'Turbines RFP', vendorCount: 3 },
]

function setup(selected = new Set([1, 2, 3]), onChange = vi.fn()) {
  render(<ProjectFilter projects={projects} selectedIds={selected} onChange={onChange} />)
  return onChange
}

async function open() {
  await userEvent.click(screen.getByRole('button', { name: /projects/i }))
}

test('trigger summarizes all selected', () => {
  setup()
  expect(screen.getByRole('button', { name: /all \(3\)/i })).toBeInTheDocument()
})

test('trigger summarizes a subset', () => {
  setup(new Set([1]))
  expect(screen.getByRole('button', { name: /1 of 3/i })).toBeInTheDocument()
})

test('search narrows the option rows', async () => {
  setup()
  await open()
  await userEvent.type(screen.getByRole('searchbox', { name: /filter projects/i }), 'hvac')
  expect(screen.getByText('HVAC Q3')).toBeInTheDocument()
  expect(screen.queryByText('Bridge job')).toBeNull()
})

test('toggling an option emits the new selection', async () => {
  const onChange = setup(new Set([1, 2, 3]))
  await open()
  await userEvent.click(screen.getByText('Bridge job'))
  expect(onChange).toHaveBeenCalledWith(new Set([2, 3]))
})

test('deselect all emits empty set', async () => {
  const onChange = setup(new Set([1, 2, 3]))
  await open()
  await userEvent.click(screen.getByRole('button', { name: /deselect all/i }))
  expect(onChange).toHaveBeenCalledWith(new Set())
})

test('select all emits every id', async () => {
  const onChange = setup(new Set([1]))
  await open()
  await userEvent.click(screen.getByRole('button', { name: /select all/i }))
  expect(onChange).toHaveBeenCalledWith(new Set([1, 2, 3]))
})

test('shows vendor counts on rows', async () => {
  setup()
  await open()
  expect(screen.getByText(/5 vendors/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/test/projectFilter.test.tsx`
Expected: FAIL — cannot resolve `../components/ProjectFilter`.

- [ ] **Step 3: Implement**

```typescript
// frontend/src/components/ProjectFilter.tsx
import { useState } from 'react'
import { filterByName } from '../filter'

export interface FilterProject { id: number; name: string; vendorCount: number }

export function ProjectFilter({
  projects, selectedIds, onChange,
}: {
  projects: FilterProject[]
  selectedIds: Set<number>
  onChange: (ids: Set<number>) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const visible = filterByName(projects, query)
  const total = projects.length
  const sel = selectedIds.size
  const summary = sel === total ? `all (${total})` : `${sel} of ${total}`

  const toggle = (id: number) => {
    const next = new Set(selectedIds)
    next.has(id) ? next.delete(id) : next.add(id)
    onChange(next)
  }

  return (
    <div className="project-filter">
      <button className="filter-trigger" onClick={() => setOpen((o) => !o)}
              aria-haspopup="true" aria-expanded={open}>
        Projects <span className="count">· {summary}</span> <span className="caret">▾</span>
      </button>
      {open && (
        <div className="filter-dropdown">
          <input
            className="filter-search" type="search" aria-label="Filter projects"
            placeholder="Filter projects…"
            value={query} onChange={(e) => setQuery(e.target.value)}
          />
          <div className="filter-actions">
            <button className="link" onClick={() => onChange(new Set(projects.map((p) => p.id)))}>
              Select all
            </button>
            <button className="link" onClick={() => onChange(new Set())}>Deselect all</button>
          </div>
          <div className="filter-options">
            {visible.map((p) => {
              const on = selectedIds.has(p.id)
              return (
                <button key={p.id} className={`filter-opt${on ? ' on' : ''}`}
                        onClick={() => toggle(p.id)} role="checkbox" aria-checked={on}>
                  <span className="box">{on ? '✓' : ''}</span>
                  <span className="opt-name">{p.name}</span>
                  <span className="vcount">{p.vendorCount} vendors</span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/projectFilter.test.tsx`
Expected: PASS (7 tests)

- [ ] **Step 5: Add styles**

Append to `frontend/src/styles.css` (mirrors mockup `.filter*` classes):

```css
.project-filter { position: relative; display: inline-block; margin-bottom: 16px; }
.filter-trigger { display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font-size: 13px; cursor: pointer; }
.filter-trigger:hover { border-color: var(--accent); }
.filter-trigger .count { color: var(--text-muted); }
.filter-trigger .caret { font-size: 10px; color: var(--text-muted); }
.filter-dropdown { position: absolute; top: calc(100% + 6px); left: 0; z-index: 30; width: 260px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.12); padding: 10px; }
.filter-dropdown .filter-search { width: 100%; box-sizing: border-box; padding: 7px 10px; border: 1px solid var(--input-border); border-radius: 6px; font-size: 13px; background: var(--bg); color: var(--text); margin-bottom: 8px; }
.filter-actions { display: flex; gap: 4px; margin-bottom: 6px; border-bottom: 1px solid var(--border-soft); padding-bottom: 8px; }
.filter-actions .link { border: none; background: none; color: var(--accent); font-size: 12px; cursor: pointer; padding: 2px 6px; }
.filter-actions .link:hover { text-decoration: underline; }
.filter-options { max-height: 260px; overflow-y: auto; }
.filter-opt { display: flex; align-items: center; gap: 9px; padding: 7px 8px; border-radius: 6px; font-size: 13px; cursor: pointer; width: 100%; border: none; background: none; color: var(--text); text-align: left; }
.filter-opt:hover { background: var(--surface); }
.filter-opt .box { width: 15px; height: 15px; border: 1.5px solid var(--input-border); border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; font-size: 10px; color: #fff; flex-shrink: 0; }
.filter-opt.on .box { background: var(--accent); border-color: var(--accent); }
.filter-opt .opt-name { flex: 1; }
.filter-opt .vcount { font-size: 11px; color: var(--text-faint); }
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ProjectFilter.tsx frontend/src/test/projectFilter.test.tsx frontend/src/styles.css
git commit -m "feat: ProjectFilter multi-select dropdown with search + select/deselect all"
```

---

### Task B3: Dashboard types, API, and view

**Files:**
- Create: `frontend/src/dashboard.ts`, `frontend/src/components/Dashboard.tsx`, `frontend/src/test/dashboard.test.tsx`
- Modify: `frontend/src/api.ts`, `frontend/src/styles.css`

- [ ] **Step 1: Add types + API method**

Create `frontend/src/dashboard.ts`:

```typescript
export interface VendorRef { vendor_id: number; name: string; project_id: number; project_name: string }
export interface ShortlistEntry { ref: VendorRef; verdict: number; previous_verdict: number | null }
export interface RedFlag { ref: VendorRef; kind: 'dimension' | 'delivery_risk'; dimension: string | null; score: number; detail: string }
export interface TrustedVendor { name: string; vendor_key: string; chosen_count: number; project_count: number; verdict: number | null }

export interface DashboardStats {
  projects_total: number; projects_selected: number
  vendors_total: number; vendors_generated: number
  avg_verdict: number | null
  risk_high: number; risk_watch: number; risk_cleared: number
  independent_sources: number; self_reported_sources: number
  verdict_histogram: number[]
  dimension_avgs: Record<string, number>
  dimension_histograms: Record<string, number[]>
  weakest_dimension: string | null
  weakest_low_count: number
  shortlist: ShortlistEntry[]
  red_flags: RedFlag[]
  most_trusted: TrustedVendor[]
}
```

In `api.ts`, add the import and method (inside the `api` object):

```typescript
import type { DashboardStats } from './dashboard'

  getStats: (projectIds?: number[]) => {
    const q = projectIds && projectIds.length ? `?projects=${projectIds.join(',')}` : ''
    return fetch(`${BASE}/stats${q}`).then(json<DashboardStats>)
  },
  setChosen: (id: number, chosen: boolean) =>
    fetch(`${BASE}/vendors/${id}/chosen`, {
      method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ chosen }),
    }).then(json<VendorOut>),
```

(Add `VendorOut` to the existing `types` import if not already present.)

- [ ] **Step 2: Write the failing test**

```typescript
// frontend/src/test/dashboard.test.tsx
import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Dashboard } from '../components/Dashboard'
import type { DashboardStats } from '../dashboard'

const stats: DashboardStats = {
  projects_total: 3, projects_selected: 3, vendors_total: 14, vendors_generated: 12,
  avg_verdict: 6.4, risk_high: 2, risk_watch: 4, risk_cleared: 6,
  independent_sources: 71, self_reported_sources: 29,
  verdict_histogram: [0, 0, 1, 1, 0, 2, 2, 4, 1, 1, 0],
  dimension_avgs: { legal: 6.8, safety: 4.1, financial: 6.3, backlog: 7.2, certifications: 7.7, news: 6.6 },
  dimension_histograms: {
    legal: [0, 0, 0, 1, 0, 1, 2, 3, 2, 1, 0], safety: [0, 1, 2, 3, 2, 2, 1, 1, 0, 0, 0],
    financial: [0, 0, 1, 0, 1, 2, 3, 2, 2, 0, 0], backlog: [0, 0, 0, 0, 1, 1, 2, 3, 3, 1, 0],
    certifications: [0, 0, 0, 0, 0, 1, 1, 2, 3, 2, 0], news: [0, 0, 1, 0, 1, 1, 2, 3, 2, 1, 0],
  },
  weakest_dimension: 'safety', weakest_low_count: 3,
  shortlist: [{ ref: { vendor_id: 1, name: 'Bechtel', project_id: 1, project_name: 'Bridge job' }, verdict: 9, previous_verdict: 8 }],
  red_flags: [{ ref: { vendor_id: 2, name: 'Fluor', project_id: 1, project_name: 'Bridge job' }, kind: 'dimension', dimension: 'safety', score: 2, detail: 'Safety 2' }],
  most_trusted: [{ name: 'Bechtel', vendor_key: 'bechtel.com', chosen_count: 4, project_count: 5, verdict: 9 }],
}

test('renders the headline verdict and coverage', () => {
  render(<Dashboard stats={stats} onOpenVendor={() => {}} />)
  expect(screen.getByText('6.4')).toBeInTheDocument()
  expect(screen.getByText(/12 of 14/)).toBeInTheDocument()
})

test('renders decisions: shortlist, red flags, most trusted', () => {
  render(<Dashboard stats={stats} onOpenVendor={() => {}} />)
  expect(screen.getByText('Bechtel')).toBeInTheDocument()
  expect(screen.getByText('Fluor')).toBeInTheDocument()
  expect(screen.getByText(/chosen 4/i)).toBeInTheDocument()
})

test('names the weakest dimension', () => {
  render(<Dashboard stats={stats} onOpenVendor={() => {}} />)
  expect(screen.getByText(/safety/i)).toBeInTheDocument()
})

test('empty-selection state when nothing selected', () => {
  const empty = { ...stats, projects_selected: 0, vendors_total: 0, vendors_generated: 0, avg_verdict: null }
  render(<Dashboard stats={empty} onOpenVendor={() => {}} />)
  expect(screen.getByText(/select at least one project/i)).toBeInTheDocument()
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd frontend && npx vitest run src/test/dashboard.test.tsx`
Expected: FAIL — cannot resolve `../components/Dashboard`.

- [ ] **Step 4: Implement the Dashboard**

```typescript
// frontend/src/components/Dashboard.tsx
import type { DashboardStats, VendorRef } from '../dashboard'
import { bandForScore } from '../band'
import { DIMENSIONS } from '../dimensions'

const LABEL = new Map(DIMENSIONS.map((d) => [d.key, d.label]))
const bandColor = (score: number) => `var(--${bandForScore(score) === 'good' ? 'good' : bandForScore(score) === 'mid' ? 'mid' : 'bad'})`

function Histogram({ hist }: { hist: number[] }) {
  const max = Math.max(1, ...hist)
  return (
    <div className="hist">
      {hist.map((n, score) => (
        <div key={score} className="col">
          <span className="count">{n || ''}</span>
          <span className="bar" style={{ height: `${(n / max) * 100}%`, background: bandColor(score) }} />
          <span className="n">{score}</span>
        </div>
      ))}
    </div>
  )
}

export function Dashboard({ stats, onOpenVendor }: {
  stats: DashboardStats
  onOpenVendor: (ref: VendorRef) => void
}) {
  if (stats.projects_selected === 0) {
    return <p className="empty">Select at least one project to see the overview.</p>
  }
  if (stats.vendors_generated === 0) {
    return <p className="empty">No researched vendors yet in the selected projects.</p>
  }
  const s = stats
  const totalSrc = s.independent_sources + s.self_reported_sources
  const indPct = totalSrc ? Math.round((s.independent_sources / totalSrc) * 100) : 0

  return (
    <div className="dashboard">
      <div className="cards">
        <div className="card">
          <div className="label">Portfolio verdict</div>
          <div className="big">{s.avg_verdict?.toFixed(1) ?? '—'} <small>/ 10</small>
            {s.avg_verdict != null && <span className={`pill ${bandForScore(Math.round(s.avg_verdict))}`}>
              {bandForScore(Math.round(s.avg_verdict)) === 'good' ? 'Cleared' : bandForScore(Math.round(s.avg_verdict)) === 'mid' ? 'Watch' : 'High risk'}
            </span>}
          </div>
          <div className="hint">avg across {s.vendors_generated} researched vendors</div>
        </div>
        <div className="card">
          <div className="label">Researched</div>
          <div className="big">{s.vendors_generated} <small>of {s.vendors_total}</small></div>
          <div className="hint">{s.vendors_total - s.vendors_generated} pending</div>
        </div>
        <div className="card">
          <div className="label">Risk triage</div>
          <div className="triage">
            <span style={{ flex: s.risk_high || 0.001, background: 'var(--bad)' }} />
            <span style={{ flex: s.risk_watch || 0.001, background: 'var(--mid)' }} />
            <span style={{ flex: s.risk_cleared || 0.001, background: 'var(--good)' }} />
          </div>
          <div className="legend">
            <span><i className="swatch" style={{ background: 'var(--bad)' }} /><b>{s.risk_high}</b> high</span>
            <span><i className="swatch" style={{ background: 'var(--mid)' }} /><b>{s.risk_watch}</b> watch</span>
            <span><i className="swatch" style={{ background: 'var(--good)' }} /><b>{s.risk_cleared}</b> cleared</span>
          </div>
        </div>
        <div className="card">
          <div className="label">Evidence sources</div>
          <div className="split">
            <span style={{ flex: s.independent_sources || 0.001, background: 'var(--accent)' }} />
            <span style={{ flex: s.self_reported_sources || 0.001, background: 'var(--bar)' }} />
          </div>
          <div className="legend">
            <span><i className="swatch" style={{ background: 'var(--accent)' }} /><b>{indPct}%</b> independent</span>
            <span><i className="swatch" style={{ background: 'var(--bar)' }} /><b>{100 - indPct}%</b> self-reported</span>
          </div>
        </div>
      </div>

      <div className="decisions">
        <div className="chart-card">
          <div className="label">Shortlist</div>
          <div className="caption">highest verdicts</div>
          <div className="dlist">
            {s.shortlist.map((e, i) => (
              <div key={e.ref.vendor_id} className="drow good" role="button" tabIndex={0}
                   onClick={() => onOpenVendor(e.ref)}
                   onKeyDown={(ev) => { if (ev.key === 'Enter') onOpenVendor(e.ref) }}>
                <span className="medal">{i + 1}</span>
                <span className="vname">{e.ref.name}</span>
                <span className="why">{e.ref.project_name}</span>
                <span className="score">{e.verdict}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="chart-card">
          <div className="label">Red flags</div>
          <div className="caption">any dimension ≤3, or weak backlog + financial</div>
          <div className="dlist">
            {s.red_flags.length === 0 && <p className="muted">None — nothing scored ≤3.</p>}
            {s.red_flags.map((f, i) => (
              <div key={i} className="drow bad" role="button" tabIndex={0}
                   onClick={() => onOpenVendor(f.ref)}
                   onKeyDown={(ev) => { if (ev.key === 'Enter') onOpenVendor(f.ref) }}>
                <span className="vname">{f.ref.name}</span>
                <span className="why">{f.detail}{f.kind === 'delivery_risk' ? ' — delivery risk' : ''}</span>
                <span className="score">{f.score}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="chart-card">
          <div className="label">Most trusted</div>
          <div className="caption">chosen before across your projects</div>
          <div className="dlist">
            {s.most_trusted.length === 0 && <p className="muted">No vendors chosen yet.</p>}
            {s.most_trusted.map((t) => (
              <div key={t.vendor_key} className="drow">
                <span className="vname">{t.name}</span>
                <span className="trust">chosen {t.chosen_count}× · in {t.project_count} projects</span>
                <span className="score">{t.verdict ?? '—'}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="charts">
        <div className="chart-card">
          <div className="label">Verdict distribution</div>
          <div className="caption">every researched vendor's overall score</div>
          <Histogram hist={s.verdict_histogram} />
        </div>
        <div className="chart-card">
          <div className="label">Dimension averages</div>
          <div className="caption">portfolio average per axis — weakest highlighted</div>
          <div className="dims">
            {DIMENSIONS.map((d) => {
              const avg = s.dimension_avgs[d.key]
              const weak = s.weakest_dimension === d.key
              return (
                <div key={d.key} className={`dim${weak ? ' weakest' : ''}`}>
                  <span className="name">{d.label}</span>
                  <span className="track"><span className="fill" style={{ width: `${(avg ?? 0) * 10}%` }} /></span>
                  <span className="val">{avg?.toFixed(1) ?? '—'}</span>
                </div>
              )
            })}
          </div>
          {s.weakest_dimension && (
            <div className="weak-note">
              {LABEL.get(s.weakest_dimension) ?? s.weakest_dimension} is the softest axis — {s.weakest_low_count} vendors score ≤3.
            </div>
          )}
        </div>
      </div>

      <div className="multis">
        {DIMENSIONS.map((d) => (
          <div key={d.key} className={`mini${s.weakest_dimension === d.key ? ' weakest' : ''}`}>
            <div className="head">
              <span className="name">{d.label}</span>
              <span className="avg">avg {s.dimension_avgs[d.key]?.toFixed(1) ?? '—'}</span>
            </div>
            <Histogram hist={s.dimension_histograms[d.key] ?? Array(11).fill(0)} />
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/dashboard.test.tsx`
Expected: PASS (4 tests)

- [ ] **Step 6: Add styles**

Append the dashboard CSS from `scratchpad/dashboard-mockup.html` — copy the rule bodies for `.dashboard`/`.cards`/`.card`/`.big`/`.hint`/`.triage`/`.split`/`.legend`/`.swatch`/`.charts`/`.chart-card`/`.hist`/`.dims`/`.dim`/`.decisions`/`.dlist`/`.drow`/`.multis`/`.mini` into `frontend/src/styles.css`, adapting color literals to the existing CSS variables (`--good`/`--mid`/`--bad`/`--bar` — add these four to `:root` and `:root[data-theme="dark"]` if absent: `--good: #22c55e; --mid: #eab308; --bad: #ef4444; --bar: #cbd5e1;` light / `--bar: #3f4854;` dark). Keep class names identical to the mockup so the structure matches.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/dashboard.ts frontend/src/components/Dashboard.tsx frontend/src/test/dashboard.test.tsx frontend/src/api.ts frontend/src/styles.css
git commit -m "feat: Dashboard overview view (cards, decisions, bias charts)"
```

---

### Task B4: Chosen toggle in the vendor table

**Files:**
- Modify: `frontend/src/rows.ts`, `frontend/src/types.ts`, `frontend/src/components/VendorTable.tsx`, `frontend/src/components/VendorRow.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/test/vendorRow.test.tsx` (new or append)

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/test/vendorRow.test.tsx
import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VendorRow } from '../components/VendorRow'
import type { RowState } from '../rows'

function row(over: Partial<RowState> = {}): RowState {
  return {
    vendorId: 1, name: 'Acme', vendorKey: 'acme.com',
    cells: {}, verdict: 'idle', status: 'idle', chosen: false, ...over,
  }
}

function renderRow(r: RowState, onChosen = vi.fn()) {
  render(<table><tbody>
    <VendorRow row={r} onSelect={() => {}} onDelete={() => {}} onRename={() => {}}
               onResume={() => {}} onChosen={onChosen} />
  </tbody></table>)
  return onChosen
}

test('clicking the chosen toggle marks it chosen without selecting the row', async () => {
  const onSelect = vi.fn(); const onChosen = vi.fn()
  render(<table><tbody>
    <VendorRow row={row()} onSelect={onSelect} onDelete={() => {}} onRename={() => {}}
               onResume={() => {}} onChosen={onChosen} />
  </tbody></table>)
  await userEvent.click(screen.getByRole('button', { name: /mark .* chosen/i }))
  expect(onChosen).toHaveBeenCalledWith(1, true)
  expect(onSelect).not.toHaveBeenCalled()
})

test('a chosen vendor shows the un-choose control', () => {
  renderRow(row({ chosen: true }))
  expect(screen.getByRole('button', { name: /unmark .* chosen/i })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/test/vendorRow.test.tsx`
Expected: FAIL — `onChosen` prop / `chosen` field absent.

- [ ] **Step 3: Implement**

In `types.ts`, add `chosen: boolean` to `VendorSummary` and `chosen: boolean` to `VendorOut`.

In `rows.ts`, add `chosen: boolean` to `RowState`, and set it in `rowFromSummary` (default from summary):

```typescript
// in the base RowState:
    duplicateOf: v.duplicate_of ?? null,
    chosen: v.chosen ?? false,
```

(Add `chosen` to the `VendorSummary` fields read; the two return objects both spread `base`, so it carries.)

In `VendorTable.tsx`, thread an `onChosen` prop through to `VendorRow`:

```typescript
  rows, onSelect, onDelete, onRename, onResume, onChosen,
```

add `onChosen: (id: number, chosen: boolean) => void` to the props type, and pass `onChosen={onChosen}` on the `<VendorRow ... />`.

In `VendorRow.tsx`, add `onChosen` to props and render a toggle button in the actions cell (before the delete button):

```tsx
        <button
          className={`icon-btn chosen-btn${row.chosen ? ' is-chosen' : ''}`}
          aria-label={row.chosen ? `Unmark ${row.name} as chosen` : `Mark ${row.name} as chosen`}
          title={row.chosen ? 'Chosen — click to unmark' : 'Mark as chosen'}
          onClick={(e) => { e.stopPropagation(); onChosen(row.vendorId, !row.chosen) }}
        >
          {row.chosen ? '★' : '☆'}
        </button>
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/vendorRow.test.tsx`
Expected: PASS (2 tests)

- [ ] **Step 5: Add styles**

Append to `frontend/src/styles.css`:

```css
.chosen-btn { color: var(--text-faint); font-size: 15px; }
.chosen-btn.is-chosen { color: var(--mid, #eab308); }
.chosen-btn:hover { color: var(--mid, #eab308); }
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/rows.ts frontend/src/types.ts frontend/src/components/VendorTable.tsx frontend/src/components/VendorRow.tsx frontend/src/test/vendorRow.test.tsx frontend/src/styles.css
git commit -m "feat: mark-as-chosen star toggle on vendor rows"
```

---

### Task B5: App wiring — Overview landing + ReportPanel trust/deltas

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/components/ReportPanel.tsx`, `frontend/src/types.ts`
- Test: `frontend/src/test/sidebar.test.tsx` (append), manual App wiring covered at integration

- [ ] **Step 1: Sidebar "Overview" entry — write the failing test**

Append to `frontend/src/test/sidebar.test.tsx`:

```typescript
test('shows an Overview entry that is active when no project is selected', async () => {
  const onOverview = vi.fn()
  render(
    <Sidebar projects={projects} activeId={null} onSelect={() => {}} onCreate={() => {}}
             onDelete={() => {}} onOverview={onOverview} />,
  )
  const overview = screen.getByRole('button', { name: /overview/i })
  expect(overview).toHaveClass('active')
  await userEvent.click(overview)
  expect(onOverview).toHaveBeenCalled()
})
```

Update the existing `renderSidebar` helper to pass `onOverview: props.onOverview ?? (() => {})`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npx vitest run src/test/sidebar.test.tsx`
Expected: FAIL — no Overview button / `onOverview` prop.

- [ ] **Step 3: Implement Sidebar entry**

In `Sidebar.tsx` props, add `onOverview: () => void`. Render above the search input (after `<h4>Projects</h4>` — actually place it above the heading as a nav item):

```tsx
      <button
        className={`overview-entry${activeId === null ? ' active' : ''}`}
        onClick={onOverview}
      >
        ▤ Overview
      </button>
```

Add style to `styles.css`:

```css
.overview-entry { display: block; width: 100%; text-align: left; padding: 8px 10px; margin-bottom: 10px; border: none; border-radius: 6px; background: none; color: var(--text); font-size: 13px; cursor: pointer; }
.overview-entry:hover { background: var(--hover-strong); }
.overview-entry.active { background: var(--accent-bg); font-weight: 600; }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/test/sidebar.test.tsx`
Expected: PASS (all sidebar tests).

- [ ] **Step 5: Wire App.tsx**

In `App.tsx`:

1. Add imports: `import { Dashboard } from './components/Dashboard'`, `import { ProjectFilter } from './components/ProjectFilter'`, and dashboard types.
2. Add state: `const [stats, setStats] = useState<DashboardStats | null>(null)` and `const [selectedProjectIds, setSelectedProjectIds] = useState<Set<number>>(new Set())`.
3. Remove the first-project auto-select in the load-projects effect — change:
   ```typescript
   setActiveId((cur) => (cur == null && ps.length ? ps[0].id : cur))
   ```
   to just `setProjects(ps)` and initialize `selectedProjectIds` to all ids:
   ```typescript
   setProjects(ps)
   setSelectedProjectIds(new Set(ps.map((p) => p.id)))
   ```
   (Leave `activeId` at its initial `null` so the app lands on the Overview.)
4. Add an effect to fetch stats when on the Overview (`activeId === null`) or the selection changes:
   ```typescript
   useEffect(() => {
     if (activeId !== null) return
     api.getStats([...selectedProjectIds])
       .then(setStats)
       .catch(() => setError('Could not load the overview.'))
   }, [activeId, selectedProjectIds])
   ```
5. Add the chosen handler (optimistic):
   ```typescript
   async function setChosen(vendorId: number, chosen: boolean) {
     dispatch({ kind: 'upsert', row: { ...rows.find((r) => r.vendorId === vendorId)!, chosen } })
     try { await api.setChosen(vendorId, chosen) }
     catch { setError('Could not update chosen.'); }
   }
   ```
   (If `rows.find` is undefined-risky, guard it.)
6. Add `onOpenVendor` for dashboard drill-through:
   ```typescript
   function openVendorFromDashboard(ref: VendorRef) {
     setActiveId(ref.project_id)
     setSelectedVendorId(ref.vendor_id)
   }
   ```
7. Render: pass `onOverview={() => setActiveId(null)}` to `<Sidebar>`, pass `onChosen={setChosen}` to `<VendorTable>`, and replace the `activeProject ? (...) : (...)` branch's else-clause so that when `activeId === null` it renders the ProjectFilter + Dashboard:
   ```tsx
   ) : (
     <>
       <div className="toolbar">
         <h3>Overview</h3>
         <ThemeToggle theme={theme} onToggle={toggleTheme} />
       </div>
       <ProjectFilter
         projects={projects.map((p) => ({ id: p.id, name: p.name, vendorCount: 0 }))}
         selectedIds={selectedProjectIds}
         onChange={setSelectedProjectIds}
       />
       {stats ? <Dashboard stats={stats} onOpenVendor={openVendorFromDashboard} />
              : <p className="muted">Loading overview…</p>}
     </>
   )
   ```
   (vendorCount can be 0 for now; integration task can populate it from a project list count if desired.)

- [ ] **Step 6: ReportPanel trust + deltas**

In `types.ts`, extend `VendorReport`:

```typescript
export interface DimensionDelta { score: number; recorded_on: string }
// add to VendorReport:
  chosen_count?: number
  projects_count?: number
  dimension_deltas?: Record<string, DimensionDelta | null>
```

Thread these onto `RowState` via `report` is not ideal — instead, since the panel reads from `row`, carry the two counts + deltas on `RowState` (add `chosenCount?`, `projectsCount?`, `deltas?`) and set them in `selectVendor`/`rowFromReport`. Minimal approach: in `App.selectVendor`, after fetching, dispatch them onto the row. For the plan's scope, add to `RowState`:

```typescript
  chosenCount?: number
  projectsCount?: number
  deltas?: Record<string, { score: number; recorded_on: string } | null>
```

In `rowFromReport` and the `setReport` path, copy `r.chosen_count`, `r.projects_count`, `r.dimension_deltas` onto the row.

In `ReportPanel.tsx`, in the header block under the name, show the trust line when present:

```tsx
          {typeof row.projectsCount === 'number' && row.projectsCount > 0 && (
            <div className="panel-trust">
              In {row.projectsCount} project{row.projectsCount === 1 ? '' : 's'}
              {row.chosenCount ? ` · chosen ${row.chosenCount}×` : ''}
            </div>
          )}
```

In the per-section header, add a delta marker when a prior score exists:

```tsx
                <h5>{LABEL.get(s.dimension) ?? s.dimension} · {s.score}/10
                  {row.deltas?.[s.dimension] && (
                    <span className="delta">
                      {' '}{s.score >= row.deltas[s.dimension]!.score ? '▲' : '▼'}
                      {Math.abs(s.score - row.deltas[s.dimension]!.score)} since {row.deltas[s.dimension]!.recorded_on}
                    </span>
                  )}
                </h5>
```

Add styles:

```css
.panel-trust { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.delta { font-size: 11px; color: var(--text-faint); font-weight: 400; }
```

- [ ] **Step 7: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS (all). Fix any type errors (`npx tsc --noEmit` if the project runs it).

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat: Overview landing view, dashboard drill-through, chosen wiring, report trust+deltas"
```

---

# INTEGRATION

### Task I1: Merge both lanes + live verification

**Files:** whole repo. No new production code unless contract drift is found.

- [ ] **Step 1: Merge**

Merge Lane A and Lane B branches into an integration branch (or main per the finishing skill). Resolve trivially — the only shared file is `docs/`.

- [ ] **Step 2: Wipe the dev DB (schema changed — new column + table)**

```bash
rm -f backend/.vendor_dd_cache.db backend/.vendor_dd_cache.db-wal backend/.vendor_dd_cache.db-shm
```

- [ ] **Step 3: Full suites, both sides**

Run: `cd backend && python -m pytest -q`
Run: `cd frontend && npx vitest run`
Expected: all green.

- [ ] **Step 4: Start both servers**

Backend: `cd backend && uvicorn vendor_dd.surfaces.api.app:build_app --factory --reload` (port 8000).
Frontend: `cd frontend && npm run dev` (port 5173).

- [ ] **Step 5: Live browser verification** (use `localhost`, not `127.0.0.1` — Vite binds IPv6)

Verify, at `http://localhost:5173/`:
1. App lands on **Overview** (not a project) — dashboard shows or an empty state.
2. Create 2 projects, add + generate 2–3 real vendors across them (real Tavily/LLM calls).
3. Overview cards populate; histograms render; weakest dimension highlighted; shortlist/red-flags/most-trusted correct.
4. Project filter: search narrows, select/deselect all works, deselect-all shows the empty state, subset changes the numbers.
5. Star-toggle a vendor in a project; return to Overview → "Most trusted" reflects it; report panel header shows "In N projects · chosen M×".
6. Re-generate a vendor (⟳) and confirm no crash; deltas appear only after a second distinct day (note: same-day re-gen won't show a delta — expected).
7. Click a shortlist/red-flag row → navigates into that project with the report panel open.
8. Toggle dark mode — dashboard colors legible in both.

- [ ] **Step 6: Finish the branch**

Use superpowers:finishing-a-development-branch to merge to main.

---

## Self-Review Notes (author)

- **Spec coverage:** filter dropdown (B2), stat cards (B3), decisions band (B3), bias charts incl. mini-histograms (B3), mark-as-chosen (A3+B4), score_history with enum-CHECK + REPLACE idempotency (A1), app-layer history writes (A2), `/stats?projects=` with unknown-id/malformed handling (A5), report trust+deltas (A6+B5), Overview landing + no auto-select (B5), shared filterByName (B1). All present.
- **Type consistency:** `DashboardStats` fields match between `dashboard.py` (Pydantic) and `dashboard.ts` (TS). `VendorRow` is the backend-internal input dataclass (not serialized). `chosen` added to Vendor/VendorOut/VendorSummary on both sides. `dimension_deltas` keyed by dimension string on both sides.
- **Risk bands** (≤3 / 4–6 / ≥7) consistent with `bandForScore` (good≥7, mid≥4, bad else) — the same thresholds.
- **Weakest-dimension tie rule:** `min` over `SCORED_DIMS` in enum order is stable → first (enum-earliest) wins; tested.
- **History best-effort:** wrapped in try/except in `_record_score`; a write failure logs and continues.
