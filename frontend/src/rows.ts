import type { EntityCard, Report, ReportStreamEvent, Section, VendorSummary } from './types'
import { DIMENSIONS } from './dimensions'

export type CellState = 'idle' | 'pending' | 'failed' | { score: number }

export interface RowState {
  vendorId: number
  name: string
  vendorKey: string | null
  entity?: EntityCard
  cells: Record<string, CellState>
  verdict: CellState
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
    // Safe to unconditionally reset cells/verdict to pending: ReportEngine.iter_events always yields EntityResolved as the very first event, before any dimension fan-out begins, so this case never fires after section_complete/section_error events.
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
    case 'report_error': {
      // Flip any cells still awaiting a result to failed too — a dropped/errored stream
      // means nothing more is coming for them. Cells that already scored successfully
      // (or already failed) are left untouched.
      const cells = Object.fromEntries(
        Object.entries(row.cells).map(([k, v]) => [k, v === 'pending' ? 'failed' : v]),
      )
      return { ...row, status: 'error', errorMsg: ev.message, verdict: 'failed', cells }
    }
  }
}
