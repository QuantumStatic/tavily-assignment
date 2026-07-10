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
          className="link-btn"
          aria-label="Delete vendor"
          onClick={(e) => { e.stopPropagation(); onDelete(row.vendorId) }}
        >
          ✕
        </button>
      </td>
    </tr>
  )
}
