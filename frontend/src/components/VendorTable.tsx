import type { RowState } from '../rows'
import { DIMENSIONS, VERDICT_HELP } from '../dimensions'
import { VendorRow } from './VendorRow'

function HelpBadge({ text }: { text: string }) {
  return (
    <span className="col-help" tabIndex={0} role="img" aria-label={text}>
      ?
      <span className="col-tip" aria-hidden="true">{text}</span>
    </span>
  )
}

export function VendorTable({
  rows, onSelect, onDelete,
}: { rows: RowState[]; onSelect: (id: number) => void; onDelete: (id: number) => void }) {
  return (
    <div className="table-scroll">
      <table className="vendor-table">
        <thead>
          <tr>
            <th scope="col"><span className="col-label">Vendor</span></th>
            <th scope="col"><span className="col-label">Verdict</span><HelpBadge text={VERDICT_HELP} /></th>
            {DIMENSIONS.map((d) => (
              <th key={d.key} scope="col">
                <span className="col-label">{d.label}</span><HelpBadge text={d.help} />
              </th>
            ))}
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
    </div>
  )
}
