import { useEffect, useReducer, useRef, useState } from 'react'
import type { Project } from './types'
import type { ReportStreamEvent } from './types'
import type { RowState } from './rows'
import { rowFromSummary, startStreaming, reduceEvent } from './rows'
import { DIMENSIONS } from './dimensions'
import { api } from './api'
import { openReportStream } from './stream'
import { Sidebar } from './components/Sidebar'
import { AddVendorForm } from './components/AddVendorForm'
import { VendorTable } from './components/VendorTable'
import { ReportPanel } from './components/ReportPanel'
import { ThemeToggle } from './components/ThemeToggle'
import { useTheme } from './theme'

type RowsAction =
  | { kind: 'set'; rows: RowState[] }
  | { kind: 'upsert'; row: RowState }
  | { kind: 'remove'; vendorId: number }
  | { kind: 'event'; vendorId: number; ev: ReportStreamEvent }
  | { kind: 'setReport'; vendorId: number; report: RowState['report']; entity: RowState['entity']
      sectionsPresent?: number; sectionsExpected?: number }

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
    case 'setReport':
      return state.map((r) => r.vendorId === action.vendorId
        ? { ...r, report: action.report, entity: action.entity ?? r.entity,
            sectionsPresent: action.sectionsPresent ?? r.sectionsPresent,
            sectionsExpected: action.sectionsExpected ?? r.sectionsExpected } : r)
  }
}

export default function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [rows, dispatch] = useReducer(rowsReducer, [])
  const [selectedVendorId, setSelectedVendorId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [theme, toggleTheme] = useTheme()
  const streams = useRef<Map<number, () => void>>(new Map())
  const activeIdRef = useRef(activeId)

  // keep activeIdRef in sync with activeId
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  // load projects once
  useEffect(() => {
    api.listProjects()
      .then((ps) => {
        setProjects(ps)
        setActiveId((cur) => (cur == null && ps.length ? ps[0].id : cur))
      })
      .catch(() => setError('Could not reach the API. Is the backend running?'))
  }, [])

  // load the active project's vendor rows
  useEffect(() => {
    if (activeId == null) return
    let cancelled = false
    setSelectedVendorId(null)
    api.getProject(activeId)
      .then((detail) => {
        if (cancelled) return
        dispatch({ kind: 'set', rows: detail.vendors.map(rowFromSummary) })
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

  async function addVendor(name: string) {
    if (activeId == null) return
    const forProject = activeId
    try {
      const v = await api.addVendor(forProject, name)
      if (activeIdRef.current !== forProject) return   // user switched projects while this was in flight
      const row = startStreaming(rowFromSummary({
        vendor_id: v.id, name: v.name, vendor_key: v.vendor_key,
        generated: false, sections_present: 0, sections_expected: DIMENSIONS.length,
        verdict_score: null, verdict_reasoning: null, dimensions: [],
      }))
      dispatch({ kind: 'upsert', row })
      const close = openReportStream(
        v.id,
        (ev) => dispatch({ kind: 'event', vendorId: v.id, ev }),
        () => {
          dispatch({ kind: 'event', vendorId: v.id, ev: { type: 'report_error', message: 'stream dropped' } })
          setError('The report stream dropped — the row is marked failed. Delete and re-add to retry.')
        },
      )
      streams.current.set(v.id, close)
    } catch (e) {
      // surface the API's message (e.g. "Vendor already added to this project")
      setError(e instanceof Error ? e.message : 'Could not add the vendor.')
    }
  }

  async function removeVendor(vendorId: number) {
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

  async function selectVendor(vendorId: number) {
    setSelectedVendorId(vendorId)
    const row = rows.find((r) => r.vendorId === vendorId)
    if (!row || row.status !== 'done' || row.report) return
    try {
      const fetched = await api.getReport(vendorId)
      const complete = fetched.verdict_score != null && fetched.verdict_reasoning != null && fetched.entity != null
      const report = complete ? {
        vendor_input: row.name,
        entity: fetched.entity!,
        sections: fetched.sections,
        verdict_score: fetched.verdict_score!,
        verdict_reasoning: fetched.verdict_reasoning!,
      } : undefined
      dispatch({
        kind: 'setReport', vendorId, report, entity: fetched.entity ?? undefined,
        sectionsPresent: fetched.sections_present, sectionsExpected: fetched.sections_expected,
      })
    } catch {
      setError('Could not load the report.')
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
            <VendorTable rows={sortedRows} onSelect={selectVendor} onDelete={removeVendor} />
          </>
        ) : (
          <>
            <div className="toolbar">
              <span />
              <ThemeToggle theme={theme} onToggle={toggleTheme} />
            </div>
            <p className="empty">Select a project, or create one to begin.</p>
          </>
        )}
      </main>
      {selectedRow && <ReportPanel row={selectedRow} onClose={() => setSelectedVendorId(null)} />}
    </div>
  )
}
