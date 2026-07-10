import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { DimensionCell } from './DimensionCell'
import { bandForScore } from '../band'

export function VendorRow({
  row, onSelect, onDelete,
}: { row: RowState; onSelect: (id: number) => void; onDelete: (id: number) => void }) {
  const verdict = row.verdict
  return (
    <tr
      className="vendor-row"
      tabIndex={0}
      role="button"
      onClick={() => onSelect(row.vendorId)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(row.vendorId) }
      }}
    >
      <td className="vendor-name">{row.name}</td>
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
      <td className="cell">
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
