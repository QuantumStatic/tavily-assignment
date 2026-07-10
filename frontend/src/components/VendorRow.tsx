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
