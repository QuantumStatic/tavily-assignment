import { useEffect, useReducer, useRef, useState } from 'react'
import type { Project } from './types'
import type { ReportStreamEvent, VendorReport } from './types'
import type { RowState } from './rows'
import { rowFromSummary, startStreaming, reduceEvent, rowFromReport } from './rows'
import { DIMENSIONS } from './dimensions'
import { api } from './api'
import { openReportStream } from './stream'
import type { DashboardStats, VendorRef } from './dashboard'
import { Sidebar } from './components/Sidebar'
import { AddVendorForm } from './components/AddVendorForm'
import { VendorTable } from './components/VendorTable'
import { ReportPanel } from './components/ReportPanel'
import { ThemeToggle } from './components/ThemeToggle'
import { ProjectFilter } from './components/ProjectFilter'
import { Dashboard } from './components/Dashboard'
import { TrendPanel } from './components/TrendPanel'
import { useTheme } from './theme'

type RowsAction =
  | { kind: 'set'; rows: RowState[] }
  | { kind: 'upsert'; row: RowState }
  | { kind: 'remove'; vendorId: number }
  | { kind: 'rename'; vendorId: number; name: string }
  | { kind: 'event'; vendorId: number; ev: ReportStreamEvent }
  | { kind: 'setReport'; vendorId: number; report: RowState['report']; entity: RowState['entity']
      sectionsPresent?: number; sectionsExpected?: number
      chosenCount?: number; projectsCount?: number; deltas?: RowState['deltas'] }
  | { kind: 'fromReport'; vendorId: number; report: VendorReport }

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
    case 'rename':
      return state.map((r) =>
        r.vendorId === action.vendorId ? { ...r, name: action.name } : r)
    case 'event':
      return state.map((r) =>
        r.vendorId === action.vendorId ? reduceEvent(r, action.ev) : r)
    case 'setReport':
      return state.map((r) => r.vendorId === action.vendorId
        ? { ...r, report: action.report, entity: action.entity ?? r.entity,
            sectionsPresent: action.sectionsPresent ?? r.sectionsPresent,
            sectionsExpected: action.sectionsExpected ?? r.sectionsExpected,
            chosenCount: action.chosenCount ?? r.chosenCount,
            projectsCount: action.projectsCount ?? r.projectsCount,
            deltas: action.deltas ?? r.deltas } : r)
    case 'fromReport':
      return state.map((r) =>
        r.vendorId === action.vendorId ? rowFromReport(r, action.report) : r)
  }
}

const POLL_MS = 3000
const MAX_POLLS = 100   // ~5 minutes of polling before we give up

export default function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [rows, dispatch] = useReducer(rowsReducer, [])
  const [selectedVendorId, setSelectedVendorId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [theme, toggleTheme] = useTheme()
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [selectedProjectIds, setSelectedProjectIds] = useState<Set<number>>(new Set())
  const streams = useRef<Map<number, () => void>>(new Map())
  const activeIdRef = useRef(activeId)
  // set by openVendorFromDashboard just before switching projects, so the load-rows
  // effect below can open the right vendor's report instead of clearing the selection.
  const pendingVendorRef = useRef<number | null>(null)

  // keep activeIdRef in sync with activeId
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  // load projects once — land on the Overview (activeId stays null) rather than
  // auto-selecting the first project.
  useEffect(() => {
    api.listProjects()
      .then((ps) => {
        setProjects(ps)
        setSelectedProjectIds(new Set(ps.map((p) => p.id)))
      })
      .catch(() => setError('Could not reach the API. Is the backend running?'))
  }, [])

  // fetch overview stats whenever we're on the Overview or the project selection changes
  useEffect(() => {
    if (activeId !== null) return
    let cancelled = false
    api.getStats([...selectedProjectIds])
      .then((s) => { if (!cancelled) setStats(s) })
      .catch(() => { if (!cancelled) setError('Could not load the overview.') })
    return () => { cancelled = true }
  }, [activeId, selectedProjectIds])

  // load the active project's vendor rows
  useEffect(() => {
    if (activeId == null) return
    let cancelled = false
    const pending = pendingVendorRef.current
    pendingVendorRef.current = null
    setSelectedVendorId(pending)
    api.getProject(activeId)
      .then((detail) => {
        if (cancelled) return
        dispatch({ kind: 'set', rows: detail.vendors.map(rowFromSummary) })
        // The row for `pending` was just created fresh above (no .report yet) —
        // fetch it directly from the just-loaded detail rather than going through
        // selectVendor, whose `rows` closure hasn't picked up this dispatch yet.
        if (pending != null) {
          const summary = detail.vendors.find((v) => v.vendor_id === pending)
          if (summary?.generated) void fetchAndSetReport(pending, summary.name)
        }
      })
      .catch(() => { if (!cancelled) setError('Could not load the project.') })
    return () => {
      cancelled = true
      // the outgoing project's rows are about to be replaced — close any streams
      // still running for them so nobody keeps burning Tavily/Nebius credits.
      streams.current.forEach((close) => close())
      streams.current.clear()
    }
  }, [activeId])

  // close all streams on unmount
  useEffect(() => () => { streams.current.forEach((close) => close()); streams.current.clear() }, [])

  async function createProject(name: string) {
    try {
      const p = await api.createProject(name)
      setProjects((ps) => [...ps, p])
      setActiveId(p.id)
    } catch {
      setError('Could not create the project.')
    }
  }

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

  async function addVendor(name: string) {
    if (activeId == null) return
    const forProject = activeId
    try {
      const v = await api.addVendor(forProject, name)
      if (activeIdRef.current !== forProject) return   // user switched projects while this was in flight
      if (v.existed) {
        // already in this project — same row, no new research. Just open its report.
        selectVendor(v.id)
        return
      }
      const row = startStreaming(rowFromSummary({
        vendor_id: v.id, name: v.name, vendor_key: v.vendor_key,
        generated: false, sections_present: 0, sections_expected: DIMENSIONS.length,
        verdict_score: null, verdict_reasoning: null, dimensions: [], chosen: v.chosen,
      }))
      dispatch({ kind: 'upsert', row })
      const close = openReportStream(
        v.id,
        (ev) => dispatch({ kind: 'event', vendorId: v.id, ev }),
        () => {
          // stream dropped, but the work continues server-side — recover via polling
          streams.current.delete(v.id)
          pollReport(v.id)
        },
      )
      streams.current.set(v.id, close)
    } catch (e) {
      // surface the API's message (e.g. "Vendor already added to this project")
      setError(e instanceof Error ? e.message : 'Could not add the vendor.')
    }
  }

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
        setError('Report generation stalled — press ⟳ on the row to retry.')
      }
    }
    streams.current.set(vendorId, () => window.clearInterval(id))   // project-switch/unmount cleanup
    void check()   // immediate first check: the backend may already be done
  }

  async function removeVendor(vendorId: number) {
    const row = rows.find((r) => r.vendorId === vendorId)
    if (!window.confirm(`Delete vendor "${row?.name ?? vendorId}"?`)) return
    try {
      await api.deleteVendor(vendorId)
      streams.current.get(vendorId)?.()
      streams.current.delete(vendorId)
      dispatch({ kind: 'remove', vendorId })
      if (selectedVendorId === vendorId) setSelectedVendorId(null)
    } catch {
      setError('Could not delete the vendor.')
    }
  }

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

  async function renameVendor(vendorId: number, name: string) {
    try {
      const v = await api.renameVendor(vendorId, name)
      dispatch({ kind: 'rename', vendorId, name: v.name })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not rename the vendor.')
    }
  }

  // Fetches a generated vendor's report and dispatches it onto its row. Shared by
  // selectVendor (rows already loaded) and the dashboard-drill-through paths (which
  // read the vendor's name from a freshly-fetched project detail instead of `rows`,
  // since `rows` may not contain the target project's vendors yet).
  async function fetchAndSetReport(vendorId: number, name: string) {
    try {
      const fetched = await api.getReport(vendorId)
      const complete = fetched.verdict_score != null && fetched.verdict_reasoning != null && fetched.entity != null
      const report = complete ? {
        vendor_input: name,
        entity: fetched.entity!,
        sections: fetched.sections,
        verdict_score: fetched.verdict_score!,
        verdict_reasoning: fetched.verdict_reasoning!,
      } : undefined
      dispatch({
        kind: 'setReport', vendorId, report, entity: fetched.entity ?? undefined,
        sectionsPresent: fetched.sections_present, sectionsExpected: fetched.sections_expected,
        chosenCount: fetched.chosen_count, projectsCount: fetched.projects_count,
        deltas: fetched.dimension_deltas,
      })
    } catch {
      setError('Could not load the report.')
    }
  }

  async function selectVendor(vendorId: number) {
    setSelectedVendorId(vendorId)
    const row = rows.find((r) => r.vendorId === vendorId)
    if (!row || row.status !== 'done' || row.report) return
    await fetchAndSetReport(vendorId, row.name)
  }

  async function setChosen(vendorId: number, chosen: boolean) {
    const row = rows.find((r) => r.vendorId === vendorId)
    if (!row) return
    dispatch({ kind: 'upsert', row: { ...row, chosen } })
    try {
      await api.setChosen(vendorId, chosen)
    } catch {
      setError('Could not update chosen.')
    }
  }

  function openVendorFromDashboard(ref: VendorRef) {
    if (ref.project_id === activeId) {
      void selectVendor(ref.vendor_id)
    } else {
      pendingVendorRef.current = ref.vendor_id
      setActiveId(ref.project_id)
    }
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
        onDelete={removeProject}
        onOverview={() => setActiveId(null)}
      />
      <main className="main">
        {error && (
          <div className="error-banner" role="alert" onClick={() => setError(null)}>
            <span>{error}</span>
            <button
              className="link-btn"
              aria-label="Dismiss error"
              onClick={(e) => { e.stopPropagation(); setError(null) }}
            >
              ✕
            </button>
          </div>
        )}
        {activeProject ? (
          <>
            <div className="toolbar">
              <h3>{activeProject.name}</h3>
              <div className="toolbar-actions">
                <AddVendorForm onAdd={addVendor} />
                <ThemeToggle theme={theme} onToggle={toggleTheme} />
              </div>
            </div>
            <VendorTable rows={sortedRows} onSelect={selectVendor} onDelete={removeVendor} onRename={renameVendor} onResume={resumeVendor} onChosen={setChosen} />
          </>
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
            {stats
              ? <Dashboard stats={stats} onOpenVendor={openVendorFromDashboard} />
              : <p className="muted">Loading overview…</p>}
            <TrendPanel />
          </>
        )}
      </main>
      {selectedRow && <ReportPanel row={selectedRow} onClose={() => setSelectedVendorId(null)} />}
    </div>
  )
}
