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
  | { kind: 'setReport'; vendorId: number; report: RowState['report']; entity: RowState['entity'] }

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
        ? { ...r, report: action.report, entity: action.entity ?? r.entity } : r)
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
    try {
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
    } catch {
      setError('Could not add the vendor.')
    }
  }

  async function removeVendor(vendorId: number) {
    try {
      streams.current.get(vendorId)?.()
      streams.current.delete(vendorId)
      await api.deleteVendor(vendorId)
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
      if (fetched.verdict_score == null || fetched.verdict_reasoning == null || fetched.entity == null) return
      const report = {
        vendor_input: row.name,
        entity: fetched.entity,
        sections: fetched.sections,
        verdict_score: fetched.verdict_score,
        verdict_reasoning: fetched.verdict_reasoning,
      }
      dispatch({ kind: 'setReport', vendorId, report, entity: fetched.entity })
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
        {error && <div className="error-banner" onClick={() => setError(null)}>{error}</div>}
        {activeProject ? (
          <>
            <div className="toolbar">
              <h3>{activeProject.name}</h3>
              <AddVendorForm onAdd={addVendor} />
            </div>
            <VendorTable rows={sortedRows} onSelect={selectVendor} onDelete={removeVendor} />
          </>
        ) : (
          <p className="empty">Create a project to begin.</p>
        )}
      </main>
      {selectedRow && <ReportPanel row={selectedRow} onClose={() => setSelectedVendorId(null)} />}
    </div>
  )
}
