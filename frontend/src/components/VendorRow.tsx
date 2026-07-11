import { useState } from 'react'
import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { DimensionCell } from './DimensionCell'
import { bandForScore } from '../band'

export function VendorRow({
  row, onSelect, onDelete, onRename, onResume, onChosen,
}: {
  row: RowState
  onSelect: (id: number) => void
  onDelete: (id: number) => void
  onRename: (id: number, name: string) => void
  onResume: (id: number) => void
  onChosen: (id: number, chosen: boolean) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)   // non-null while editing
  const verdict = row.verdict
  const commit = () => {
    const name = (draft ?? '').trim()
    setDraft(null)
    if (name && name !== row.name) onRename(row.vendorId, name)
  }
  // Resumable: research errored, never ran/never finished (idle), or a backend
  // restart left it partially cached. Re-opening the stream replays cached
  // sections instantly and re-researches only the missing ones.
  const resumable =
    row.status === 'error' ||
    row.status === 'idle' ||
    (row.status === 'done' && (row.sectionsPresent ?? 0) < (row.sectionsExpected ?? 0))
  return (
    <tr
      className="vendor-row"
      tabIndex={0}
      role="button"
      onClick={() => onSelect(row.vendorId)}
      onKeyDown={(e) => {
        if (draft != null) return   // typing in the rename input, not navigating
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(row.vendorId) }
      }}
    >
      <td className="vendor-name">
        {draft == null ? (
          <>
            <span>{row.name}</span>
            {row.duplicateOf && (
              <span
                className="dup-badge"
                title={`Same company as ${row.duplicateOf} — the two rows share one research report`}
              >
                ≡
              </span>
            )}
            <button
              className="icon-btn rename-btn"
              aria-label="Rename vendor"
              title="Rename vendor"
              onClick={(e) => { e.stopPropagation(); setDraft(row.name) }}
            >
              ✎
            </button>
          </>
        ) : (
          <input
            className="rename-input"
            aria-label="New vendor name"
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') commit()
              if (e.key === 'Escape') setDraft(null)
            }}
            onBlur={commit}
          />
        )}
      </td>
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
        {resumable && (
          <button
            className="icon-btn resume-btn"
            aria-label="Resume research"
            title="Resume research"
            onClick={(e) => { e.stopPropagation(); onResume(row.vendorId) }}
          >
            ⟳
          </button>
        )}
        <button
          className={`icon-btn chosen-btn${row.chosen ? ' is-chosen' : ''}`}
          aria-label={row.chosen ? `Unmark ${row.name} as chosen` : `Mark ${row.name} as chosen`}
          title={row.chosen ? 'Chosen — click to unmark' : 'Mark as chosen'}
          onClick={(e) => { e.stopPropagation(); onChosen(row.vendorId, !row.chosen) }}
        >
          {row.chosen ? '★' : '☆'}
        </button>
        <span className="action-divider" aria-hidden="true" />
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
