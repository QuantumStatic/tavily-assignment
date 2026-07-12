import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { filterByName } from '../filter'
import { useClickOutside } from '../useClickOutside'

export interface DropdownItem<Id extends string | number> {
  id: Id
  name: string
  meta?: string   // optional right-aligned detail, e.g. "5 vendors"
}

/** A searchable multi-select dropdown with select-all/deselect-all, shared by the
 *  Overview project filter and the score-trend vendor picker. Closes on outside click. */
export function MultiSelectDropdown<Id extends string | number>({
  triggerLabel, items, selectedIds, onChange, searchLabel, searchPlaceholder, emptyText,
}: {
  triggerLabel: ReactNode
  items: DropdownItem<Id>[]
  selectedIds: Set<Id>
  onChange: (ids: Set<Id>) => void
  searchLabel: string
  searchPlaceholder: string
  emptyText?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  useClickOutside(rootRef, open, () => setOpen(false))
  const visible = filterByName(items, query)

  const toggle = (id: Id) => {
    const next = new Set(selectedIds)
    next.has(id) ? next.delete(id) : next.add(id)
    onChange(next)
  }

  return (
    <div className="project-filter" ref={rootRef}>
      <button className="filter-trigger" onClick={() => setOpen((o) => !o)}
              aria-haspopup="true" aria-expanded={open}>
        {triggerLabel} <span className="caret">▾</span>
      </button>
      {open && (
        <div className="filter-dropdown">
          <input
            className="filter-search" type="search" aria-label={searchLabel}
            placeholder={searchPlaceholder}
            value={query} onChange={(e) => setQuery(e.target.value)}
          />
          <div className="filter-actions">
            <button className="link" onClick={() => onChange(new Set(items.map((i) => i.id)))}>
              Select all
            </button>
            <button className="link" onClick={() => onChange(new Set())}>Deselect all</button>
          </div>
          <div className="filter-options">
            {visible.length === 0 && emptyText && (
              <p className="muted trend-empty-options">{emptyText}</p>
            )}
            {visible.map((i) => {
              const on = selectedIds.has(i.id)
              return (
                <button key={i.id} className={`filter-opt${on ? ' on' : ''}`}
                        onClick={() => toggle(i.id)} role="checkbox" aria-checked={on}>
                  <span className="box">{on ? '✓' : ''}</span>
                  <span className="opt-name">{i.name}</span>
                  {i.meta && <span className="vcount">{i.meta}</span>}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
