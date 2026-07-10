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
              <h3>Vendors</h3>
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
