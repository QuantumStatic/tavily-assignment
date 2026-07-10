import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { VendorRow } from './VendorRow'

export function VendorTable({
  rows, onSelect, onDelete,
}: { rows: RowState[]; onSelect: (id: number) => void; onDelete: (id: number) => void }) {
  return (
    <table className="vendor-table">
      <thead>
        <tr>
          <th scope="col">Vendor</th>
          <th scope="col">Verdict</th>
          {DIMENSIONS.map((d) => <th key={d.key} scope="col">{d.label}</th>)}
          <th scope="col" aria-label="actions" />
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            <td className="empty" colSpan={DIMENSIONS.length + 3}>
              Add your first vendor to start a due-diligence report.
            </td>
          </tr>
        ) : (
          rows.map((r) => (
            <VendorRow key={r.vendorId} row={r} onSelect={onSelect} onDelete={onDelete} />
          ))
        )}
      </tbody>
    </table>
  )
}
