# Vendor Due-Diligence — Phase 3: Frontend + Tavily session_id — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a desktop React UI (sidebar of projects → vendor comparison table that fills in live over SSE → click-a-row report side panel) over the existing Phase 2 API, and fold in a small backend change so every Tavily search is grouped under a per-project `session_id` for traceability.

**Architecture:** Backend first — a per-project `session_id` (uuid) is stored on the `projects` row and injected into every Tavily `search()` call by a transparent run-level adapter around the search client (no changes to retrieval/entity/synthesis). Then the frontend: React + Vite + TS, plain CSS, no router. REST (`fetch`) for CRUD + read model, native `EventSource` for live report generation. A pure reducer maps a vendor's read-model summary + streamed events into per-dimension cell states; presentational components render them. Every seam is mockable, so the whole frontend test suite runs with a fake `EventSource` + mocked `fetch` — zero backend, zero API credits.

**Tech Stack:** Backend: Python 3.11+, sqlite3, `tavily-python` (session headers). Frontend: React 18 + Vite + TypeScript, plain CSS, Vitest + React Testing Library + jsdom.

Spec: `docs/superpowers/specs/2026-07-09-phase3-frontend-design.md`

---

## File structure

**Backend (modified for §9 session_id):**
```
backend/src/vendor_dd/
  surfaces/api/store.py     # MODIFY: + session_id column/migration, Project.session_id, id_gen
  engine/pipeline.py        # MODIFY: _SessionSearch adapter + ReportEngine(session_id=...)
  surfaces/api/routes.py    # MODIFY: stream_report passes the project's session_id
  surfaces/cli.py           # MODIFY: per-run session_id
backend/tests/
  test_store.py, test_engine.py, test_api.py   # MODIFY: session_id coverage
```

**Frontend (new — `frontend/`):**
```
frontend/
  package.json  vite.config.ts  tsconfig.json  tsconfig.node.json  index.html
  .env.example                       # VITE_API_BASE=http://localhost:8000
  README.md
  src/
    main.tsx  App.tsx  styles.css
    types.ts  dimensions.ts  api.ts  stream.ts  rows.ts
    components/
      DimensionCell.tsx  VendorRow.tsx  VendorTable.tsx
      Sidebar.tsx  NewProjectForm.tsx  AddVendorForm.tsx  ReportPanel.tsx
    test/
      setup.ts  fakeEventSource.ts
      rows.test.ts  api.test.ts  stream.test.ts
      table.test.tsx  sidebar.test.tsx  panel.test.tsx  app.test.tsx
```

**Runnable milestones:**
- After Task 4: backend groups all of a project's Tavily searches under one `session_id`; full backend suite green.
- After Task 6: `frontend/` scaffolds, `npm test` runs, types/api/dimensions compile.
- After Task 9: the comparison table renders from a read-model fixture and fills in from mocked stream events.
- After Task 13: the whole app works end-to-end against mocks (create project → add vendor → live table → row → panel).

---

## PART A — Backend: Tavily `session_id` (tracing)

## Task 1: Store — `session_id` column + `Project.session_id`

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/store.py`
- Test: `backend/tests/test_store.py` (append)

- [ ] **Step 1: Write the failing tests (append to test_store.py)**

```python
def test_create_project_persists_session_id(tmp_path):
    store = Store(tmp_path / "db.sqlite",
                  clock=lambda: "2026-07-10T00:00:00+00:00",
                  id_gen=lambda: "sess-abc")
    p = store.create_project("Bridge job")
    assert p.session_id == "sess-abc"
    assert store.get_project(p.id).session_id == "sess-abc"
    assert store.list_projects()[0].session_id == "sess-abc"


def test_session_id_defaults_to_uuid_when_no_id_gen(tmp_path):
    store = Store(tmp_path / "db.sqlite")           # real uuid id_gen
    a = store.create_project("A")
    b = store.create_project("B")
    assert a.session_id and b.session_id and a.session_id != b.session_id
    assert len(a.session_id) >= 16                  # uuid4 hex is 32 chars
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_store.py -v`
Expected: FAIL — `TypeError: Store.__init__() got an unexpected keyword argument 'id_gen'` / `Project` has no `session_id`.

- [ ] **Step 3: Implement**

In `backend/src/vendor_dd/surfaces/api/store.py`:

Add a uuid id generator near the top (after `_utcnow_iso`):
```python
import uuid


def _uuid_hex() -> str:
    return uuid.uuid4().hex
```

Add `session_id` to the `Project` dataclass (nullable so a pre-`session_id` DB can be migrated; always populated on new writes):
```python
@dataclass
class Project:
    id: int
    name: str
    created_at: str
    session_id: str | None = None
```

Change `Store.__init__` to accept `id_gen`, add the column to the CREATE, and best-effort migrate an existing DB:
```python
    def __init__(self, path: str | Path, clock: Callable[[], str] = _utcnow_iso,
                 id_gen: Callable[[], str] = _uuid_hex):
        self._clock = clock
        self._id_gen = id_gen
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 name TEXT NOT NULL,
                 created_at TEXT NOT NULL,
                 session_id TEXT
               )"""
        )
        # Migrate a DB created before session_id existed (Phase 2). No-op on fresh DBs.
        try:
            self._conn.execute("ALTER TABLE projects ADD COLUMN session_id TEXT")
        except sqlite3.OperationalError:
            pass  # column already present
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS vendors (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 project_id INTEGER NOT NULL,
                 name TEXT NOT NULL,
                 vendor_key TEXT,
                 created_at TEXT NOT NULL,
                 FOREIGN KEY (project_id) REFERENCES projects(id)
               )"""
        )
        self._conn.commit()
```

Update `create_project` to generate + store the id:
```python
    def create_project(self, name: str) -> Project:
        ts = self._clock()
        sid = self._id_gen()
        cur = self._conn.execute(
            "INSERT INTO projects (name, created_at, session_id) VALUES (?,?,?)",
            (name, ts, sid))
        self._conn.commit()
        return Project(id=cur.lastrowid, name=name, created_at=ts, session_id=sid)
```

Update the two project readers to select `session_id` (column order matches the dataclass field order):
```python
    def list_projects(self) -> list[Project]:
        cur = self._conn.execute(
            "SELECT id, name, created_at, session_id FROM projects ORDER BY id")
        return [Project(*row) for row in cur.fetchall()]

    def get_project(self, project_id: int) -> Project | None:
        cur = self._conn.execute(
            "SELECT id, name, created_at, session_id FROM projects WHERE id=?", (project_id,))
        row = cur.fetchone()
        return Project(*row) if row else None
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/test_store.py -v`
Expected: PASS (existing store tests + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/store.py backend/tests/test_store.py
git commit -m "feat(api): per-project session_id on the projects store"
```

---

## Task 2: Engine — `_SessionSearch` adapter + `ReportEngine(session_id=...)`

**Files:**
- Modify: `backend/src/vendor_dd/engine/pipeline.py`
- Test: `backend/tests/test_engine.py` (append)

- [ ] **Step 1: Write the failing tests (append to test_engine.py)**

```python
class RecordingSearch:
    """Records the kwargs of every search() call (for asserting injected headers)."""
    def __init__(self):
        self.calls = []
    def search(self, **kwargs):
        self.calls.append(kwargs)
        return {"results": [{"title": "x", "content": "Cives Steel Company",
                             "url": "https://x.com", "score": 0.8}]}


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
```

Note: `Deps`, `StatelessLLM`, `date`, `ReportEngine` are already imported/defined in `test_engine.py` from Phase 2.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_engine.py -v`
Expected: FAIL — `ReportEngine.__init__() got an unexpected keyword argument 'session_id'`.

- [ ] **Step 3: Implement**

In `backend/src/vendor_dd/engine/pipeline.py`, add the adapter after `_normalize_name`:
```python
class _SessionSearch:
    """Wraps a SearchClient to stamp run-level tracing headers (Tavily X-Session-Id /
    X-Client-Name) onto every search. Transparent: setdefault never overrides an explicit
    kwarg, and a call site that passes nothing extra behaves exactly as the inner client."""

    def __init__(self, inner: SearchClient, session_id: str):
        self._inner = inner
        self._session_id = session_id

    def search(self, **kwargs):
        kwargs.setdefault("session_id", self._session_id)
        kwargs.setdefault("client_name", "vendor-dd")
        return self._inner.search(**kwargs)
```

Change `ReportEngine.__init__` to accept `session_id` and wrap the search client once:
```python
    def __init__(self, deps: Deps, *, mode: Literal["parallel", "sequential"] = "parallel",
                 max_workers: int = 6, session_id: str | None = None):
        self._deps = deps
        self._max_workers = 1 if mode == "sequential" else max_workers
        self._search: SearchClient = (
            _SessionSearch(deps.search, session_id) if session_id else deps.search)
```

Route the engine's three search call sites through `self._search` instead of `deps.search`:

In `_compute_section`:
```python
    def _compute_section(self, dim: Dimension, entity: EntityCard) -> DimensionOutcome:
        deps = self._deps
        results = retrieve_dimension(dim, entity, search=self._search, today=deps.today)
        section = synthesize_section(dim, results, llm=deps.llm)
        return DimensionOutcome(section=section, raw_results=results)
```

In `_resolve_entity_cached` (the `resolve_entity(...)` call):
```python
        entity = resolve_entity(vendor, search=self._search, llm=deps.llm)
```

In `_backlog_section` (the fallback `retrieve_dimension(...)` call):
```python
                results = retrieve_dimension(Dimension.NEWS_POSITIVE, entity,
                                             search=self._search, today=deps.today)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/test_engine.py tests/test_pipeline.py -v`
Expected: PASS — the 2 new tests AND every existing engine/pipeline test (the adapter is a no-op when `session_id` is `None`).

Then full suite: `cd backend && uv run pytest -v` — all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/engine/pipeline.py backend/tests/test_engine.py
git commit -m "feat(engine): inject per-run session_id into Tavily searches"
```

---

## Task 3: API — `stream_report` uses the project's `session_id`

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/api/routes.py`
- Test: `backend/tests/test_api.py` (append)

- [ ] **Step 1: Write the failing test (append to test_api.py)**

```python
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
```

Note: `Deps`, `FakeLLM`, `create_app`, `TestClient`, `date` are already imported in `test_api.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_api.py::test_stream_uses_project_session_id -v`
Expected: FAIL — searches carry no `session_id` (the stream builds `ReportEngine` without one).

- [ ] **Step 3: Implement**

In `backend/src/vendor_dd/surfaces/api/routes.py`, update `stream_report` to look up the vendor's project and pass its `session_id`:
```python
@router.get("/vendors/{vendor_id}/report/stream")
def stream_report(vendor_id: int, request: Request):
    store = _store(request)
    vendor = store.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="vendor not found")
    project = store.get_project(vendor.project_id)
    session_id = project.session_id if project else None
    engine = ReportEngine(request.app.state.deps, mode="parallel", session_id=session_id)

    def event_source():
        for ev in engine.iter_events(vendor.name):
            if isinstance(ev, EntityResolved):
                key = (ev.entity.domain or ev.entity.name).strip().lower()
                store.set_vendor_key(vendor_id, key)  # backfill so the read model can join
            yield to_sse_frame(ev)

    return EventSourceResponse(event_source())
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/test_api.py -v`
Expected: PASS (existing API tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add backend/src/vendor_dd/surfaces/api/routes.py backend/tests/test_api.py
git commit -m "feat(api): stream endpoint groups searches under the project's session_id"
```

---

## Task 4: CLI — per-run `session_id`

**Files:**
- Modify: `backend/src/vendor_dd/surfaces/cli.py`
- Test: import/help check only (no live lookup — spends credits).

- [ ] **Step 1: Implement**

In `backend/src/vendor_dd/surfaces/cli.py`, add a uuid import at the top with the other stdlib imports:
```python
import uuid
```

Change the engine construction (currently `engine = ReportEngine(deps, mode="parallel")`) to generate a per-run session id:
```python
    # One session_id per CLI run groups this invocation's ~8 Tavily searches together.
    engine = ReportEngine(deps, mode="parallel", session_id=uuid.uuid4().hex)
```

- [ ] **Step 2: Verify it imports (no live call)**

Run: `cd backend && uv run python -c "from vendor_dd.surfaces.cli import app; print('ok')"`
Expected: `ok`

Run: `cd backend && uv run vendor-dd --help`
Expected: Typer help text. (Does not invoke a report — no credits.)

- [ ] **Step 3: Confirm suite still green**

Run: `cd backend && uv run pytest -v`
Expected: all green, unchanged count.

- [ ] **Step 4: Commit**

```bash
git add backend/src/vendor_dd/surfaces/cli.py
git commit -m "feat(cli): per-run session_id for Tavily tracing"
```

---

## PART B — Frontend

## Task 5: Scaffold the Vite + React + TS app with Vitest

**Files:** create the `frontend/` project skeleton + test harness.

- [ ] **Step 1: Create project files**

`frontend/package.json`:
```json
{
  "name": "vendor-dd-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.8",
    "@testing-library/react": "^16.0.1",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^25.0.0",
    "typescript": "^5.5.4",
    "vite": "^5.4.2",
    "vitest": "^2.0.5"
  }
}
```

`frontend/vite.config.ts`:
```ts
/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
```

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`frontend/tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

`frontend/index.html`:
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Vendor Due-Diligence</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/.env.example`:
```
VITE_API_BASE=http://localhost:8000
```

`frontend/src/main.tsx`:
```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

`frontend/src/vite-env.d.ts` (so `import.meta.env` type-checks under `tsc`):
```ts
/// <reference types="vite/client" />
```

`frontend/src/App.tsx` (placeholder replaced in Task 13, but must compile now):
```tsx
export default function App() {
  return <div>Vendor Due-Diligence</div>
}
```

`frontend/src/styles.css`:
```css
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, sans-serif; }
```

- [ ] **Step 2: Test harness**

`frontend/src/test/setup.ts`:
```ts
import '@testing-library/jest-dom'
import { FakeEventSource } from './fakeEventSource'

// jsdom has no EventSource; install the controllable fake globally.
;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource
```

`frontend/src/test/fakeEventSource.ts`:
```ts
type Listener = (e: MessageEvent) => void

// Minimal controllable EventSource: tests grab the latest instance and push frames.
export class FakeEventSource {
  static instances: FakeEventSource[] = []
  static last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }
  static reset() { FakeEventSource.instances = [] }

  url: string
  closed = false
  onerror: (() => void) | null = null
  private listeners: Record<string, Listener[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: Listener) {
    ;(this.listeners[type] ||= []).push(fn)
  }
  close() { this.closed = true }

  // --- test controls ---
  emit(type: string, data: unknown) {
    for (const fn of this.listeners[type] || [])
      fn({ data: JSON.stringify(data) } as MessageEvent)
  }
  fail() { this.onerror?.() }
}
```

- [ ] **Step 3: Install and verify**

Run: `cd frontend && npm install`
Expected: installs cleanly.

Run: `cd frontend && npm test`
Expected: Vitest runs and reports **no test files found** (exit 0 with "No test files found" is acceptable at this step) — or add a trivial `src/test/smoke.test.ts` with `import {expect,test} from 'vitest'; test('boots',()=>expect(1).toBe(1))` to confirm the runner works, then leave it.

Run: `cd frontend && npm run build`
Expected: type-checks and builds with no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "chore(frontend): scaffold Vite + React + TS + Vitest"
```

---

## Task 6: `types.ts`, `dimensions.ts`, `api.ts`

**Files:**
- Create: `frontend/src/types.ts`, `frontend/src/dimensions.ts`, `frontend/src/api.ts`
- Test: `frontend/src/test/api.test.ts`

- [ ] **Step 1: Write the failing test**

`frontend/src/test/api.test.ts`:
```ts
import { afterEach, expect, test, vi } from 'vitest'
import { api } from '../api'

afterEach(() => vi.restoreAllMocks())

test('createProject POSTs name and returns the project', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ id: 1, name: 'P', created_at: 't' }),
  })
  vi.stubGlobal('fetch', fetchMock)
  const p = await api.createProject('P')
  expect(p.id).toBe(1)
  const [url, init] = fetchMock.mock.calls[0]
  expect(String(url)).toMatch(/\/projects$/)
  expect(init.method).toBe('POST')
  expect(JSON.parse(init.body)).toEqual({ name: 'P' })
})

test('getProject GETs the detail read-model', async () => {
  const detail = { id: 1, name: 'P', created_at: 't', vendors: [] }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => detail }))
  expect(await api.getProject(1)).toEqual(detail)
})

test('a non-ok response rejects', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }))
  await expect(api.getProject(9)).rejects.toThrow()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- api`
Expected: FAIL — cannot find module `../api`.

- [ ] **Step 3: Implement**

`frontend/src/types.ts`:
```ts
export interface Project { id: number; name: string; created_at: string }

export interface DimensionScore { dimension: string; score: number; as_of: string }

export interface VendorSummary {
  vendor_id: number
  name: string
  vendor_key: string | null
  generated: boolean
  verdict_score: number | null
  verdict_reasoning: string | null
  dimensions: DimensionScore[]
}

export interface ProjectDetail {
  id: number; name: string; created_at: string; vendors: VendorSummary[]
}

export interface VendorOut {
  id: number; project_id: number; name: string; vendor_key: string | null; created_at: string
}

export interface Citation {
  url: string; title: string; source_type: string; score: number; as_of: string | null
}
export interface Finding { claim: string; citation: Citation }
export interface Section { dimension: string; findings: Finding[]; reasoning: string; score: number }

export interface EntityCard {
  name: string; domain: string | null; country: string | null; industry: string | null
  parent: string | null; is_public: boolean; ticker: string | null; exchange: string | null
}

export interface VendorReport {
  generated: boolean
  vendor_key: string | null
  entity: EntityCard | null
  verdict_score: number | null
  verdict_reasoning: string | null
  sections: Section[]
}

// The engine Report carried in a report_complete SSE frame.
export interface Report {
  vendor_input: string
  entity: EntityCard
  sections: Section[]
  verdict_score: number
  verdict_reasoning: string
}

// SSE frame payloads, discriminated by `type` (the SSE event name, merged in by stream.ts).
export type ReportStreamEvent =
  | { type: 'entity_resolved'; entity: EntityCard }
  | { type: 'section_complete'; section: Section; cached: boolean }
  | { type: 'section_error'; dimension: string; message: string }
  | { type: 'report_complete'; report: Report }
  | { type: 'report_error'; message: string }
```

`frontend/src/dimensions.ts`:
```ts
// Column order + labels for the comparison table. One entry per scored backend Dimension
// (excludes `snapshot`, which is entity resolution, not a report section).
export const DIMENSIONS: { key: string; label: string }[] = [
  { key: 'legal', label: 'Legal' },
  { key: 'financial', label: 'Financial' },
  { key: 'safety', label: 'Safety' },
  { key: 'certifications', label: 'Certs' },
  { key: 'backlog', label: 'Backlog/Ops' },
  { key: 'news_positive', label: 'News +' },
  { key: 'news_negative', label: 'News −' },
]
```

`frontend/src/api.ts`:
```ts
import type { Project, ProjectDetail, VendorOut, VendorReport } from './types'

const BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`request failed: ${r.status}`)
  return r.json() as Promise<T>
}

const JSON_HEADERS = { 'content-type': 'application/json' }

export const api = {
  base: BASE,
  streamUrl: (vendorId: number) => `${BASE}/vendors/${vendorId}/report/stream`,

  listProjects: () => fetch(`${BASE}/projects`).then(json<Project[]>),
  createProject: (name: string) =>
    fetch(`${BASE}/projects`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ name }) })
      .then(json<Project>),
  getProject: (id: number) => fetch(`${BASE}/projects/${id}`).then(json<ProjectDetail>),
  addVendor: (projectId: number, name: string) =>
    fetch(`${BASE}/projects/${projectId}/vendors`, {
      method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ name }),
    }).then(json<VendorOut>),
  deleteVendor: (id: number) =>
    fetch(`${BASE}/vendors/${id}`, { method: 'DELETE' }).then((r) => {
      if (!r.ok) throw new Error(`request failed: ${r.status}`)
    }),
  getReport: (id: number) => fetch(`${BASE}/vendors/${id}/report`).then(json<VendorReport>),
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- api`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/dimensions.ts frontend/src/api.ts frontend/src/test/api.test.ts
git commit -m "feat(frontend): API types, dimensions, and typed fetch client"
```

---

## Task 7: `stream.ts` — EventSource opener

**Files:**
- Create: `frontend/src/stream.ts`
- Test: `frontend/src/test/stream.test.ts`

- [ ] **Step 1: Write the failing test**

`frontend/src/test/stream.test.ts`:
```ts
import { beforeEach, expect, test, vi } from 'vitest'
import { openReportStream } from '../stream'
import { FakeEventSource } from './fakeEventSource'

beforeEach(() => FakeEventSource.reset())

test('dispatches typed events and closes on report_complete', () => {
  const events: string[] = []
  openReportStream(1, (ev) => events.push(ev.type), () => {})
  const es = FakeEventSource.last()
  expect(es.url).toMatch(/\/vendors\/1\/report\/stream$/)

  es.emit('entity_resolved', { entity: { name: 'Cives' } })
  es.emit('section_complete', { section: { dimension: 'legal', score: 8, findings: [], reasoning: '' }, cached: false })
  es.emit('report_complete', { report: { verdict_score: 7, sections: [] } })

  expect(events).toEqual(['entity_resolved', 'section_complete', 'report_complete'])
  expect(es.closed).toBe(true)   // auto-closed on terminal event
})

test('onerror closes without reopening (credit-safety)', () => {
  const onError = vi.fn()
  openReportStream(2, () => {}, onError)
  const es = FakeEventSource.last()
  es.fail()
  expect(onError).toHaveBeenCalledOnce()
  expect(es.closed).toBe(true)
})

test('the returned dispose closes the stream', () => {
  const dispose = openReportStream(3, () => {}, () => {})
  const es = FakeEventSource.last()
  dispose()
  expect(es.closed).toBe(true)
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- stream`
Expected: FAIL — cannot find module `../stream`.

- [ ] **Step 3: Implement**

`frontend/src/stream.ts`:
```ts
import type { ReportStreamEvent } from './types'
import { api } from './api'

const EVENT_TYPES = [
  'entity_resolved', 'section_complete', 'section_error', 'report_complete', 'report_error',
] as const

/**
 * Open an SSE stream for a vendor's report. Calls onEvent for each frame; auto-closes on
 * the terminal report_complete/report_error. On a connection error it closes and calls
 * onError instead of letting the native EventSource auto-reconnect (a reconnect would
 * restart the backend generator and re-spend Tavily/Nebius credits). Returns a dispose fn.
 */
export function openReportStream(
  vendorId: number,
  onEvent: (ev: ReportStreamEvent) => void,
  onError: () => void,
): () => void {
  const es = new EventSource(api.streamUrl(vendorId))
  for (const type of EVENT_TYPES) {
    es.addEventListener(type, (e: MessageEvent) => {
      const data = JSON.parse(e.data)
      onEvent({ type, ...data } as ReportStreamEvent)
      if (type === 'report_complete' || type === 'report_error') es.close()
    })
  }
  es.onerror = () => { es.close(); onError() }
  return () => es.close()
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- stream`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stream.ts frontend/src/test/stream.test.ts
git commit -m "feat(frontend): SSE report-stream opener (no auto-reconnect)"
```

---

## Task 8: `rows.ts` — the read-model + event → row-state reducer

**Files:**
- Create: `frontend/src/rows.ts`
- Test: `frontend/src/test/rows.test.ts`

- [ ] **Step 1: Write the failing test**

`frontend/src/test/rows.test.ts`:
```ts
import { expect, test } from 'vitest'
import { rowFromSummary, startStreaming, reduceEvent } from '../rows'
import type { VendorSummary } from '../types'

const summary = (over: Partial<VendorSummary> = {}): VendorSummary => ({
  vendor_id: 1, name: 'Cives', vendor_key: 'cives.com', generated: false,
  verdict_score: null, verdict_reasoning: null, dimensions: [], ...over,
})

test('rowFromSummary: not generated -> idle cells, idle verdict', () => {
  const r = rowFromSummary(summary())
  expect(r.status).toBe('idle')
  expect(r.verdict).toBe('idle')
  expect(r.cells.legal).toBe('idle')
})

test('rowFromSummary: generated -> scored cells + verdict', () => {
  const r = rowFromSummary(summary({
    generated: true, verdict_score: 6, verdict_reasoning: 'ok',
    dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
  }))
  expect(r.status).toBe('done')
  expect(r.verdict).toEqual({ score: 6 })
  expect(r.cells.legal).toEqual({ score: 8 })
})

test('streaming lifecycle: pending -> scored -> failed -> complete', () => {
  let r = startStreaming(rowFromSummary(summary()))
  expect(r.status).toBe('streaming')

  r = reduceEvent(r, { type: 'entity_resolved', entity: { name: 'Cives' } as never })
  expect(r.cells.legal).toBe('pending')

  r = reduceEvent(r, {
    type: 'section_complete',
    section: { dimension: 'legal', score: 8, findings: [], reasoning: '' }, cached: false,
  })
  expect(r.cells.legal).toEqual({ score: 8 })

  r = reduceEvent(r, { type: 'section_error', dimension: 'financial', message: 'boom' })
  expect(r.cells.financial).toBe('failed')

  r = reduceEvent(r, {
    type: 'report_complete',
    report: { vendor_input: 'Cives', entity: { name: 'Cives' } as never,
              sections: [{ dimension: 'legal', score: 8, findings: [], reasoning: '' }],
              verdict_score: 7, verdict_reasoning: 'r' },
  })
  expect(r.status).toBe('done')
  expect(r.verdict).toEqual({ score: 7 })
  expect(r.report?.verdict_score).toBe(7)
})

test('report_error marks the row failed', () => {
  const r = reduceEvent(startStreaming(rowFromSummary(summary())),
    { type: 'report_error', message: 'fatal' })
  expect(r.status).toBe('error')
  expect(r.verdict).toBe('failed')
  expect(r.errorMsg).toBe('fatal')
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- rows`
Expected: FAIL — cannot find module `../rows`.

- [ ] **Step 3: Implement**

`frontend/src/rows.ts`:
```ts
import type { EntityCard, Report, ReportStreamEvent, Section, VendorSummary } from './types'
import { DIMENSIONS } from './dimensions'

export type CellState = 'idle' | 'pending' | 'failed' | { score: number }

export interface RowState {
  vendorId: number
  name: string
  vendorKey: string | null
  entity?: EntityCard
  cells: Record<string, CellState>
  verdict: 'idle' | 'pending' | 'failed' | { score: number }
  status: 'idle' | 'streaming' | 'done' | 'error'
  errorMsg?: string
  report?: Report
}

const cellsWith = (value: CellState): Record<string, CellState> =>
  Object.fromEntries(DIMENSIONS.map((d) => [d.key, value]))

const cellsFromSections = (sections: Section[]): Record<string, CellState> => {
  const scored = new Map(sections.map((s) => [s.dimension, s.score]))
  return Object.fromEntries(
    DIMENSIONS.map((d) => [d.key, scored.has(d.key) ? { score: scored.get(d.key)! } : 'failed']),
  )
}

/** Build a row from the persisted read-model summary (no stream running). */
export function rowFromSummary(v: VendorSummary): RowState {
  const base: RowState = {
    vendorId: v.vendor_id, name: v.name, vendorKey: v.vendor_key,
    cells: cellsWith('idle'), verdict: 'idle', status: 'idle',
  }
  if (!v.generated) return base
  const cells = cellsWith('failed')
  for (const d of v.dimensions) cells[d.dimension] = { score: d.score }
  return {
    ...base, cells, status: 'done',
    verdict: v.verdict_score == null ? 'idle' : { score: v.verdict_score },
  }
}

/** Transition a row into the streaming state (all cells pending). */
export function startStreaming(row: RowState): RowState {
  return { ...row, status: 'streaming', verdict: 'pending', cells: cellsWith('pending'),
           errorMsg: undefined, report: undefined }
}

/** Apply one SSE event to a row, returning the next row state. */
export function reduceEvent(row: RowState, ev: ReportStreamEvent): RowState {
  switch (ev.type) {
    case 'entity_resolved':
      return { ...row, entity: ev.entity, status: 'streaming',
               cells: cellsWith('pending'), verdict: 'pending' }
    case 'section_complete':
      return { ...row, cells: { ...row.cells, [ev.section.dimension]: { score: ev.section.score } } }
    case 'section_error':
      return { ...row, cells: { ...row.cells, [ev.dimension]: 'failed' } }
    case 'report_complete':
      return { ...row, status: 'done', report: ev.report,
               entity: ev.report.entity, cells: cellsFromSections(ev.report.sections),
               verdict: { score: ev.report.verdict_score } }
    case 'report_error':
      return { ...row, status: 'error', errorMsg: ev.message, verdict: 'failed' }
  }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- rows`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/rows.ts frontend/src/test/rows.test.ts
git commit -m "feat(frontend): row-state reducer for read-model + stream events"
```

---

## Task 9: Table components — `DimensionCell`, `VendorRow`, `VendorTable`

**Files:**
- Create: `frontend/src/components/DimensionCell.tsx`, `VendorRow.tsx`, `VendorTable.tsx`
- Add CSS to `frontend/src/styles.css`
- Test: `frontend/src/test/table.test.tsx`

- [ ] **Step 1: Write the failing test**

`frontend/src/test/table.test.tsx`:
```tsx
import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VendorTable } from '../components/VendorTable'
import { rowFromSummary } from '../rows'
import type { VendorSummary } from '../types'

const summary = (over: Partial<VendorSummary>): VendorSummary => ({
  vendor_id: 1, name: 'Cives', vendor_key: 'cives.com', generated: false,
  verdict_score: null, verdict_reasoning: null, dimensions: [], ...over,
})

test('renders a header per dimension plus vendor + verdict', () => {
  render(<VendorTable rows={[]} onSelect={() => {}} onDelete={() => {}} />)
  expect(screen.getByText('Legal')).toBeInTheDocument()
  expect(screen.getByText('News +')).toBeInTheDocument()
  expect(screen.getByText('Verdict')).toBeInTheDocument()
})

test('renders a scored row and fires onSelect on click', async () => {
  const row = rowFromSummary(summary({
    generated: true, verdict_score: 7,
    dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
  }))
  const onSelect = vi.fn()
  render(<VendorTable rows={[row]} onSelect={onSelect} onDelete={() => {}} />)
  expect(screen.getByText('Cives')).toBeInTheDocument()
  expect(screen.getByText('8')).toBeInTheDocument()
  await userEvent.click(screen.getByText('Cives'))
  expect(onSelect).toHaveBeenCalledWith(1)
})

test('a streaming row shows a pending indicator', () => {
  const row = { ...rowFromSummary(summary({})), status: 'streaming' as const,
                cells: { legal: 'pending' as const }, verdict: 'pending' as const }
  render(<VendorTable rows={[row]} onSelect={() => {}} onDelete={() => {}} />)
  expect(screen.getByTestId('cell-legal-pending')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- table`
Expected: FAIL — cannot find `../components/VendorTable`.

- [ ] **Step 3: Implement**

`frontend/src/components/DimensionCell.tsx`:
```tsx
import type { CellState } from '../rows'

export function DimensionCell({ dim, state }: { dim: string; state: CellState }) {
  if (state === 'idle') return <td className="cell idle">—</td>
  if (state === 'pending')
    return <td className="cell"><span className="dot" data-testid={`cell-${dim}-pending`} /></td>
  if (state === 'failed')
    return <td className="cell"><span className="failed" title="failed">✗</span></td>
  const band = state.score >= 7 ? 'good' : state.score >= 4 ? 'mid' : 'bad'
  return <td className="cell"><span className={`pill ${band}`}>{state.score}</span></td>
}
```

`frontend/src/components/VendorRow.tsx`:
```tsx
import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { DimensionCell } from './DimensionCell'

export function VendorRow({
  row, onSelect, onDelete,
}: { row: RowState; onSelect: (id: number) => void; onDelete: (id: number) => void }) {
  const verdict = row.verdict
  return (
    <tr className="vendor-row" onClick={() => onSelect(row.vendorId)}>
      <td className="vendor-name">{row.name}</td>
      <td className="cell">
        {verdict === 'idle' ? '—'
          : verdict === 'pending' ? <span className="dot" />
          : verdict === 'failed' ? <span className="failed">✗</span>
          : <span className={`pill ${verdict.score >= 7 ? 'good' : verdict.score >= 4 ? 'mid' : 'bad'}`}>
              {verdict.score}/10
            </span>}
      </td>
      {DIMENSIONS.map((d) => (
        <DimensionCell key={d.key} dim={d.key} state={row.cells[d.key] ?? 'idle'} />
      ))}
      <td className="cell">
        <button className="link-btn" onClick={(e) => { e.stopPropagation(); onDelete(row.vendorId) }}>
          ✕
        </button>
      </td>
    </tr>
  )
}
```

`frontend/src/components/VendorTable.tsx`:
```tsx
import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { VendorRow } from './VendorRow'

export function VendorTable({
  rows, onSelect, onDelete,
}: { rows: RowState[]; onSelect: (id: number) => void; onDelete: (id: number) => void }) {
  if (rows.length === 0)
    return <p className="empty">Add your first vendor to start a due-diligence report.</p>
  return (
    <table className="vendor-table">
      <thead>
        <tr>
          <th>Vendor</th>
          <th>Verdict</th>
          {DIMENSIONS.map((d) => <th key={d.key}>{d.label}</th>)}
          <th aria-label="actions" />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <VendorRow key={r.vendorId} row={r} onSelect={onSelect} onDelete={onDelete} />
        ))}
      </tbody>
    </table>
  )
}
```

Append to `frontend/src/styles.css`:
```css
.vendor-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.vendor-table th, .vendor-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e5e5e5; }
.vendor-table th { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: #888; }
.vendor-row { cursor: pointer; }
.vendor-row:hover { background: #f6f8fa; }
.vendor-name { font-weight: 600; }
.cell.idle { color: #bbb; }
.pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-weight: 600; font-size: 12px; }
.pill.good { background: #dcfce7; color: #166534; }
.pill.mid { background: #fef9c3; color: #854d0e; }
.pill.bad { background: #fee2e2; color: #991b1b; }
.failed { color: #dc2626; font-weight: 700; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #999; animation: pulse 1.2s infinite; }
@keyframes pulse { 0%,100% { opacity: .3 } 50% { opacity: 1 } }
.link-btn { border: none; background: none; cursor: pointer; color: #999; font-size: 12px; }
.link-btn:hover { color: #dc2626; }
.empty { color: #888; padding: 24px; }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- table`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DimensionCell.tsx frontend/src/components/VendorRow.tsx frontend/src/components/VendorTable.tsx frontend/src/styles.css frontend/src/test/table.test.tsx
git commit -m "feat(frontend): vendor comparison table components"
```

---

## Task 10: `Sidebar` + `NewProjectForm`

**Files:**
- Create: `frontend/src/components/Sidebar.tsx`, `frontend/src/components/NewProjectForm.tsx`
- Add CSS to `frontend/src/styles.css`
- Test: `frontend/src/test/sidebar.test.tsx`

- [ ] **Step 1: Write the failing test**

`frontend/src/test/sidebar.test.tsx`:
```tsx
import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sidebar } from '../components/Sidebar'

const projects = [
  { id: 1, name: 'Bridge job', created_at: 't' },
  { id: 2, name: 'HVAC Q3', created_at: 't' },
]

test('lists projects and highlights the active one', () => {
  render(<Sidebar projects={projects} activeId={2} onSelect={() => {}} onCreate={() => {}} />)
  expect(screen.getByText('Bridge job')).toBeInTheDocument()
  expect(screen.getByText('HVAC Q3').closest('li')).toHaveClass('active')
})

test('clicking a project selects it', async () => {
  const onSelect = vi.fn()
  render(<Sidebar projects={projects} activeId={1} onSelect={onSelect} onCreate={() => {}} />)
  await userEvent.click(screen.getByText('HVAC Q3'))
  expect(onSelect).toHaveBeenCalledWith(2)
})

test('the new-project form submits a trimmed name and clears', async () => {
  const onCreate = vi.fn()
  render(<Sidebar projects={[]} activeId={null} onSelect={() => {}} onCreate={onCreate} />)
  const input = screen.getByPlaceholderText('New project…')
  await userEvent.type(input, '  Q1 RFP  {enter}')
  expect(onCreate).toHaveBeenCalledWith('Q1 RFP')
  expect(input).toHaveValue('')
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- sidebar`
Expected: FAIL — cannot find `../components/Sidebar`.

- [ ] **Step 3: Implement**

`frontend/src/components/NewProjectForm.tsx`:
```tsx
import { useState } from 'react'

export function NewProjectForm({ onCreate }: { onCreate: (name: string) => void }) {
  const [name, setName] = useState('')
  return (
    <form
      className="new-project"
      onSubmit={(e) => {
        e.preventDefault()
        const trimmed = name.trim()
        if (!trimmed) return
        onCreate(trimmed)
        setName('')
      }}
    >
      <input
        placeholder="New project…"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
    </form>
  )
}
```

`frontend/src/components/Sidebar.tsx`:
```tsx
import type { Project } from '../types'
import { NewProjectForm } from './NewProjectForm'

export function Sidebar({
  projects, activeId, onSelect, onCreate,
}: {
  projects: Project[]
  activeId: number | null
  onSelect: (id: number) => void
  onCreate: (name: string) => void
}) {
  return (
    <aside className="sidebar">
      <h4>Projects</h4>
      <ul className="project-list">
        {projects.map((p) => (
          <li
            key={p.id}
            className={p.id === activeId ? 'active' : ''}
            onClick={() => onSelect(p.id)}
          >
            {p.name}
          </li>
        ))}
      </ul>
      <NewProjectForm onCreate={onCreate} />
    </aside>
  )
}
```

Append to `frontend/src/styles.css`:
```css
.sidebar { width: 220px; min-width: 220px; background: #fafafa; border-right: 1px solid #e5e5e5; padding: 12px; }
.sidebar h4 { margin: 0 0 10px; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #999; }
.project-list { list-style: none; margin: 0; padding: 0; }
.project-list li { padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.project-list li:hover { background: #eee; }
.project-list li.active { background: #e0ecff; font-weight: 600; }
.new-project { margin-top: 12px; }
.new-project input { width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- sidebar`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/NewProjectForm.tsx frontend/src/styles.css frontend/src/test/sidebar.test.tsx
git commit -m "feat(frontend): projects sidebar + new-project form"
```

---

## Task 11: `AddVendorForm`

**Files:**
- Create: `frontend/src/components/AddVendorForm.tsx`
- Add CSS to `frontend/src/styles.css`
- Test: extend `frontend/src/test/sidebar.test.tsx` (or a new `addvendor.test.tsx`)

- [ ] **Step 1: Write the failing test**

`frontend/src/test/addvendor.test.tsx`:
```tsx
import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AddVendorForm } from '../components/AddVendorForm'

test('submits a trimmed vendor name and clears the input', async () => {
  const onAdd = vi.fn()
  render(<AddVendorForm onAdd={onAdd} />)
  const input = screen.getByPlaceholderText('Vendor name…')
  await userEvent.type(input, '  Cives Steel  ')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  expect(onAdd).toHaveBeenCalledWith('Cives Steel')
  expect(input).toHaveValue('')
})

test('does not submit an empty name', async () => {
  const onAdd = vi.fn()
  render(<AddVendorForm onAdd={onAdd} />)
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  expect(onAdd).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- addvendor`
Expected: FAIL — cannot find `../components/AddVendorForm`.

- [ ] **Step 3: Implement**

`frontend/src/components/AddVendorForm.tsx`:
```tsx
import { useState } from 'react'

export function AddVendorForm({ onAdd }: { onAdd: (name: string) => void }) {
  const [name, setName] = useState('')
  return (
    <form
      className="add-vendor"
      onSubmit={(e) => {
        e.preventDefault()
        const trimmed = name.trim()
        if (!trimmed) return
        onAdd(trimmed)
        setName('')
      }}
    >
      <input placeholder="Vendor name…" value={name} onChange={(e) => setName(e.target.value)} />
      <button type="submit">+ Add vendor</button>
    </form>
  )
}
```

Append to `frontend/src/styles.css`:
```css
.add-vendor { display: flex; gap: 8px; }
.add-vendor input { flex: 1; padding: 6px 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; }
.add-vendor button { padding: 6px 14px; border: 1px solid #b9d0ff; background: #e0ecff; border-radius: 6px; cursor: pointer; font-size: 13px; }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- addvendor`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/AddVendorForm.tsx frontend/src/styles.css frontend/src/test/addvendor.test.tsx
git commit -m "feat(frontend): add-vendor form"
```

---

## Task 12: `ReportPanel`

**Files:**
- Create: `frontend/src/components/ReportPanel.tsx`
- Add CSS to `frontend/src/styles.css`
- Test: `frontend/src/test/panel.test.tsx`

- [ ] **Step 1: Write the failing test**

`frontend/src/test/panel.test.tsx`:
```tsx
import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReportPanel } from '../components/ReportPanel'
import type { RowState } from '../rows'

const row: RowState = {
  vendorId: 1, name: 'Cives', vendorKey: 'cives.com',
  cells: {}, verdict: { score: 7 }, status: 'done',
  entity: { name: 'Cives Steel', domain: 'cives.com', country: 'us', industry: 'steel',
            parent: null, is_public: false, ticker: null, exchange: null },
  report: {
    vendor_input: 'Cives', verdict_score: 7, verdict_reasoning: 'Solid overall.',
    entity: { name: 'Cives Steel' } as never,
    sections: [{
      dimension: 'legal', score: 8, reasoning: 'clean',
      findings: [{ claim: 'No active litigation.',
                   citation: { url: 'https://pacer.gov', title: 'PACER', source_type: 'independent', score: 0.9, as_of: '2026-06' } }],
    }],
  },
}

test('renders verdict, findings and a citation link', () => {
  render(<ReportPanel row={row} onClose={() => {}} />)
  expect(screen.getByText(/Cives Steel/)).toBeInTheDocument()
  expect(screen.getByText(/Solid overall/)).toBeInTheDocument()
  expect(screen.getByText('No active litigation.')).toBeInTheDocument()
  const link = screen.getByRole('link', { name: /pacer/i })
  expect(link).toHaveAttribute('href', 'https://pacer.gov')
  expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
})

test('close button fires onClose', async () => {
  const onClose = vi.fn()
  render(<ReportPanel row={row} onClose={onClose} />)
  await userEvent.click(screen.getByRole('button', { name: /close/i }))
  expect(onClose).toHaveBeenCalledOnce()
})

test('a streaming row with no report yet shows a progress note', () => {
  render(<ReportPanel row={{ ...row, status: 'streaming', report: undefined }} onClose={() => {}} />)
  expect(screen.getByText(/generating/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- panel`
Expected: FAIL — cannot find `../components/ReportPanel`.

- [ ] **Step 3: Implement**

`frontend/src/components/ReportPanel.tsx`:
```tsx
import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'

const LABEL = new Map(DIMENSIONS.map((d) => [d.key, d.label]))

export function ReportPanel({ row, onClose }: { row: RowState; onClose: () => void }) {
  const report = row.report
  const entity = row.entity
  return (
    <aside className="panel">
      <div className="panel-head">
        <div>
          <strong>{entity?.name ?? row.name}</strong>
          {typeof row.verdict === 'object' && <span className="pill good"> {row.verdict.score}/10</span>}
        </div>
        <button className="link-btn" onClick={onClose} aria-label="Close">✕</button>
      </div>

      {row.status === 'error' && <p className="failed">Report failed: {row.errorMsg}</p>}

      {!report && row.status === 'streaming' && <p className="muted">Generating report…</p>}

      {report && (
        <>
          <p className="verdict-reason">{report.verdict_reasoning}</p>
          {report.sections.map((s) => (
            <section key={s.dimension} className="report-section">
              <h5>{LABEL.get(s.dimension) ?? s.dimension} · {s.score}/10</h5>
              <p className="muted">{s.reasoning}</p>
              {s.findings.map((f, i) => (
                <div key={i} className="finding">
                  <span>{f.claim}</span>
                  <a href={f.citation.url} target="_blank" rel="noopener noreferrer">
                    ↗ {f.citation.title}
                  </a>
                  <span className="cite-meta">
                    {f.citation.source_type} · as of {f.citation.as_of ?? 'n/a'}
                  </span>
                </div>
              ))}
            </section>
          ))}
        </>
      )}
    </aside>
  )
}
```

Append to `frontend/src/styles.css`:
```css
.panel { width: 360px; min-width: 360px; border-left: 1px solid #e5e5e5; padding: 16px; overflow-y: auto; }
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.verdict-reason { font-size: 13px; }
.report-section { border-top: 1px solid #eee; padding-top: 8px; margin-top: 8px; }
.report-section h5 { margin: 0 0 4px; font-size: 13px; }
.muted { color: #888; font-size: 12px; }
.finding { display: flex; flex-direction: column; gap: 2px; padding: 6px 0; font-size: 12px; }
.finding a { color: #2563eb; text-decoration: none; }
.cite-meta { color: #aaa; font-size: 11px; }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- panel`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReportPanel.tsx frontend/src/styles.css frontend/src/test/panel.test.tsx
git commit -m "feat(frontend): vendor report side panel"
```

---

## Task 13: `App` wiring + end-to-end integration test

**Files:**
- Modify: `frontend/src/App.tsx`
- Add CSS to `frontend/src/styles.css`
- Test: `frontend/src/test/app.test.tsx`

- [ ] **Step 1: Write the failing test**

`frontend/src/test/app.test.tsx`:
```tsx
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import { FakeEventSource } from './fakeEventSource'

function mockApi() {
  const project = { id: 1, name: 'Bridge job', created_at: 't' }
  const detail = { id: 1, name: 'Bridge job', created_at: 't', vendors: [] as unknown[] }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = init?.method ?? 'GET'
    if (u.endsWith('/projects') && method === 'GET')
      return { ok: true, json: async () => [project] }
    if (u.endsWith('/projects') && method === 'POST')
      return { ok: true, json: async () => project }
    if (u.match(/\/projects\/1$/))
      return { ok: true, json: async () => detail }
    if (u.match(/\/projects\/1\/vendors$/) && method === 'POST')
      return { ok: true, json: async () => ({ id: 5, project_id: 1, name: 'Cives Steel', vendor_key: null, created_at: 't' }) }
    return { ok: true, json: async () => ({}) }
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock }
}

beforeEach(() => FakeEventSource.reset())
afterEach(() => vi.restoreAllMocks())

test('add a vendor, watch cells stream in, open the report panel', async () => {
  mockApi()
  render(<App />)

  // project loads into the sidebar and auto-selects
  await screen.findByText('Bridge job')

  // add a vendor -> POST then a stream opens
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  const row = await screen.findByText('Cives Steel')

  // drive the stream
  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  es.emit('entity_resolved', { entity: { name: 'Cives Steel', domain: 'cives.com' } })
  es.emit('section_complete', { section: { dimension: 'legal', score: 8, findings: [], reasoning: 'clean' }, cached: false })
  es.emit('report_complete', {
    report: {
      vendor_input: 'Cives Steel', verdict_score: 7, verdict_reasoning: 'Solid.',
      entity: { name: 'Cives Steel' },
      sections: [{ dimension: 'legal', score: 8, reasoning: 'clean',
                   findings: [{ claim: 'No litigation.', citation: { url: 'https://x.com', title: 'X', source_type: 'independent', score: 0.9, as_of: '2026-06' } }] }],
    },
  })

  // the verdict cell filled in
  await waitFor(() => expect(screen.getByText('7/10')).toBeInTheDocument())

  // click the row -> panel shows findings
  await userEvent.click(row)
  await screen.findByText('No litigation.')
  expect(screen.getByRole('link', { name: /X/ })).toHaveAttribute('href', 'https://x.com')
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- app`
Expected: FAIL — `App` is still the placeholder.

- [ ] **Step 3: Implement**

Replace `frontend/src/App.tsx` with:
```tsx
import { useEffect, useReducer, useRef, useState } from 'react'
import type { Project } from './types'
import type { ReportStreamEvent } from './types'
import type { RowState } from './rows'
import { rowFromSummary, startStreaming, reduceEvent } from './rows'
import { api } from './api'
import { openReportStream } from './stream'
import { Sidebar } from './components/Sidebar'
import { AddVendorForm } from './components/AddVendorForm'
import { VendorTable } from './components/VendorTable'
import { ReportPanel } from './components/ReportPanel'

type RowsAction =
  | { kind: 'set'; rows: RowState[] }
  | { kind: 'upsert'; row: RowState }
  | { kind: 'remove'; vendorId: number }
  | { kind: 'event'; vendorId: number; ev: ReportStreamEvent }

function rowsReducer(state: RowState[], action: RowsAction): RowState[] {
  switch (action.kind) {
    case 'set':
      return action.rows
    case 'upsert': {
      const rest = state.filter((r) => r.vendorId !== action.row.vendorId)
      return [...rest, action.row]
    }
    case 'remove':
      return state.filter((r) => r.vendorId !== action.vendorId)
    case 'event':
      return state.map((r) =>
        r.vendorId === action.vendorId ? reduceEvent(r, action.ev) : r)
  }
}

export default function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [rows, dispatch] = useReducer(rowsReducer, [])
  const [selectedVendorId, setSelectedVendorId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const streams = useRef<Map<number, () => void>>(new Map())

  // load projects once
  useEffect(() => {
    api.listProjects()
      .then((ps) => {
        setProjects(ps)
        if (ps.length && activeId == null) setActiveId(ps[0].id)
      })
      .catch(() => setError('Could not reach the API. Is the backend running?'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // load the active project's vendor rows
  useEffect(() => {
    if (activeId == null) return
    setSelectedVendorId(null)
    api.getProject(activeId)
      .then((detail) => dispatch({ kind: 'set', rows: detail.vendors.map(rowFromSummary) }))
      .catch(() => setError('Could not load the project.'))
  }, [activeId])

  // close all streams on unmount
  useEffect(() => () => { streams.current.forEach((close) => close()); streams.current.clear() }, [])

  async function createProject(name: string) {
    const p = await api.createProject(name)
    setProjects((ps) => [...ps, p])
    setActiveId(p.id)
  }

  async function addVendor(name: string) {
    if (activeId == null) return
    const v = await api.addVendor(activeId, name)
    const row = startStreaming(rowFromSummary({
      vendor_id: v.id, name: v.name, vendor_key: v.vendor_key,
      generated: false, verdict_score: null, verdict_reasoning: null, dimensions: [],
    }))
    dispatch({ kind: 'upsert', row })
    const close = openReportStream(
      v.id,
      (ev) => dispatch({ kind: 'event', vendorId: v.id, ev }),
      () => setError('The report stream dropped. Use ✕ and re-add the vendor to retry.'),
    )
    streams.current.set(v.id, close)
  }

  async function removeVendor(vendorId: number) {
    streams.current.get(vendorId)?.()
    streams.current.delete(vendorId)
    await api.deleteVendor(vendorId)
    dispatch({ kind: 'remove', vendorId })
    if (selectedVendorId === vendorId) setSelectedVendorId(null)
  }

  const activeProject = projects.find((p) => p.id === activeId) ?? null
  const sortedRows = [...rows].sort((a, b) => a.vendorId - b.vendorId)
  const selectedRow = rows.find((r) => r.vendorId === selectedVendorId) ?? null

  return (
    <div className="app">
      <Sidebar
        projects={projects}
        activeId={activeId}
        onSelect={setActiveId}
        onCreate={createProject}
      />
      <main className="main">
        {error && <div className="error-banner" onClick={() => setError(null)}>{error}</div>}
        {activeProject ? (
          <>
            <div className="toolbar">
              <h3>{activeProject.name}</h3>
              <AddVendorForm onAdd={addVendor} />
            </div>
            <VendorTable rows={sortedRows} onSelect={setSelectedVendorId} onDelete={removeVendor} />
          </>
        ) : (
          <p className="empty">Create a project to begin.</p>
        )}
      </main>
      {selectedRow && <ReportPanel row={selectedRow} onClose={() => setSelectedVendorId(null)} />}
    </div>
  )
}
```

Append to `frontend/src/styles.css`:
```css
.app { display: flex; height: 100vh; }
.main { flex: 1; padding: 16px 20px; overflow-y: auto; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; gap: 16px; }
.toolbar h3 { margin: 0; }
.error-banner { background: #fee2e2; color: #991b1b; padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; cursor: pointer; font-size: 13px; }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npm test -- app`
Expected: PASS.

Then the whole frontend suite + a type-check build:
Run: `cd frontend && npm test && npm run build`
Expected: all tests green; build type-checks clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css frontend/src/test/app.test.tsx
git commit -m "feat(frontend): App wiring — projects, live vendor table, report panel"
```

---

## PART C — Docs

## Task 14: Frontend README, root README refresh, roadmap (Phase 4)

**Files:**
- Create: `frontend/README.md`
- Modify: `README.md` (root)

- [ ] **Step 1: Frontend README**

`frontend/README.md`:
```markdown
# Vendor Due-Diligence — Frontend

React + Vite + TypeScript desktop UI over the Phase 2 API: a sidebar of projects, a live
vendor comparison table (fills in over SSE as each dimension completes), and a click-to-open
report side panel with cited findings.

## Run (dev)

The backend must be running first (`cd backend && uv run vendor-dd-api`, serves on :8000).

```bash
cd frontend
cp .env.example .env        # VITE_API_BASE=http://localhost:8000
npm install
npm run dev                 # http://localhost:5173
```

CORS for `localhost:5173` is already configured on the backend.

## Test

```bash
npm test        # Vitest + React Testing Library, fully mocked (no backend, no API credits)
```

## Manual smoke (spends Tavily/Nebius credits — run only when you mean to)

With the backend up: open the app, create a project, add a real vendor (e.g. "Boeing"), and
watch the row stream in. This spends ~13 credits per vendor; do not automate it.
```

- [ ] **Step 2: Refresh the root README**

In `README.md`, replace the stale status blockquote:
```markdown
> Status: scaffolding. Backend internals are stubbed pending a feasibility spike (see below).
> Design is being finalized before build.
```
with:
```markdown
> Status: backend complete (engine + CLI + FastAPI/SSE API, 64 tests) and a React frontend
> (projects, live streaming comparison table, cited report panel). Runs end-to-end locally.
```

And replace the "Next steps" checklist at the bottom with a roadmap that records what's done and
what's next (Phase 4 = tracing & logging):
```markdown
## Roadmap

- [x] Engine: retrieval → cache → synthesis → report (Phase 1)
- [x] Surfaces: CLI + FastAPI API with SSE streaming + persistence (Phase 2)
- [x] Frontend: projects, live comparison table, cited report panel (Phase 3)
- [x] Tavily per-project `session_id` for search grouping/traceability (Phase 3)
- [ ] **Phase 4 — Tracing & logging:** structured request/run logging across the engine and API,
      and end-to-end tracing (spans per report run / per dimension), aligning with
      industry-standard observability. *(next)*
- [ ] Phase 5 — Eval loop over `evals/ground_truth` (citation-support + contamination checks)
- [ ] Phase 6 — MCP server exposing `check_vendor(name)`; chat over cached `sources`
```

- [ ] **Step 3: Verify docs render / no broken references**

Run: `cd frontend && npm test` (unchanged — docs-only step; confirm suite still green)
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add frontend/README.md README.md
git commit -m "docs: frontend README + refresh root README/roadmap (Phase 4 = tracing & logging)"
```

---

## What's next (separate plans)

- **Phase 4 — Tracing & logging** (recorded in the roadmap above): structured logging + end-to-end
  tracing across engine + API. This is the next phase to spec → plan.
- Phase 5 — Eval loop over `evals/ground_truth`.
- Phase 6 — MCP server + chat over cached `sources`.

---

## Self-review notes

- **Spec coverage:**
  - §2 stack (Vite/React/TS, plain CSS, no router, EventSource, VITE_API_BASE) → Tasks 5, 6 ✓.
  - §3 layout (sidebar / table / side panel) → Tasks 9, 10, 12, 13 ✓.
  - §4 dimensions constant → Task 6 (`dimensions.ts`) ✓.
  - §5 data contracts mirrored → Task 6 (`types.ts`) ✓.
  - §6 data flow (load, add-vendor→stream, click→panel, delete) → Tasks 8, 13 ✓; verdict stays
    pending until `report_complete` → `rows.ts` reducer (only `report_complete` sets a scored verdict) ✓.
  - §7 components (Sidebar, NewProjectForm, VendorTable/Row/DimensionCell, AddVendorForm, ReportPanel,
    stream opener, api/types) → Tasks 6–13 ✓ (the spec's `useReportStream` hook is realized as the
    imperative `openReportStream` + an App-owned stream map — see deviation below).
  - §8 errors (section_error cell, report_error badge, no auto-reconnect, error banner) → Tasks 7, 8,
    12, 13 ✓.
  - §9 backend session_id (store column, engine adapter, API wiring, CLI) → Tasks 1–4 ✓.
  - §10 testing (mocked fetch + fake EventSource, table/stream/error/panel/create coverage) → Tasks
    6–13 ✓; manual smoke documented user-triggered only → Task 14 ✓.
  - §11 file structure → matches Tasks 1–14 ✓.
- **Deviations from spec (intentional):**
  1. `openReportStream` (imperative, returns a dispose fn) + an App-owned `Map<vendorId, close>`
     instead of a single `useReportStream` hook — this supports several vendors streaming
     concurrently (add A, then add B while A runs), which a one-id hook can't; it is also trivially
     unit-testable with the fake EventSource. Same lifecycle guarantees (close on terminal event,
     no auto-reconnect, close on unmount).
  2. `projects.session_id` column is nullable `TEXT` (not `NOT NULL`) so an existing Phase-2 DB can be
     migrated via `ALTER TABLE ADD COLUMN`; it is always populated on new `create_project`, and
     `Project.session_id` is `str | None`. Noted in Task 1.
  3. The report **side panel** shows full findings once `report_complete` lands (before that it shows a
     "Generating report…" note), rather than accumulating findings section-by-section as spec §6
     sketches. The **table** is what streams live (the primary UX); the panel populating on completion
     is a deliberate simplification — the reducer doesn't build a partial `report` mid-stream, and the
     verdict isn't known until the terminal event anyway. Cheap to upgrade later if desired.
- **Type consistency:** `RowState`/`CellState`, `ReportStreamEvent` (type discriminator merged from the
  SSE event name in `stream.ts`), `DIMENSIONS`, and the `api`/`types` shapes are used consistently
  across `rows.ts`, all components, and `App.tsx`. Backend: `Store(id_gen=...)`, `Project.session_id`,
  `ReportEngine(session_id=...)`, `_SessionSearch` used consistently across Tasks 1–4; the module-level
  `run_report`/`Deps` surface is unchanged so Phase 1/2 tests stay green.
- **No placeholders:** every step contains complete, runnable code.
- **Credit safety:** all backend and frontend tests use fakes/mocks; the only credit-spending step
  (frontend manual smoke, Task 14 README) is explicitly labelled user-triggered.
