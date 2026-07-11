import { useState } from 'react'
import { filterByName } from '../filter'

export interface FilterProject { id: number; name: string; vendorCount: number }

export function ProjectFilter({
  projects, selectedIds, onChange,
}: {
  projects: FilterProject[]
  selectedIds: Set<number>
  onChange: (ids: Set<number>) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const visible = filterByName(projects, query)
  const total = projects.length
  const sel = selectedIds.size
  const summary = sel === total ? `all (${total})` : `${sel} of ${total}`

  const toggle = (id: number) => {
    const next = new Set(selectedIds)
    next.has(id) ? next.delete(id) : next.add(id)
    onChange(next)
  }

  return (
    <div className="project-filter">
      <button className="filter-trigger" onClick={() => setOpen((o) => !o)}
              aria-haspopup="true" aria-expanded={open}>
        Projects <span className="count">· {summary}</span> <span className="caret">▾</span>
      </button>
      {open && (
        <div className="filter-dropdown">
          <input
            className="filter-search" type="search" aria-label="Filter projects"
            placeholder="Filter projects…"
            value={query} onChange={(e) => setQuery(e.target.value)}
          />
          <div className="filter-actions">
            {sel === total ? (
              <button className="link" onClick={() => onChange(new Set())}>Deselect all</button>
            ) : (
              <button className="link" onClick={() => onChange(new Set(projects.map((p) => p.id)))}>
                Select all
              </button>
            )}
          </div>
          <div className="filter-options">
            {visible.map((p) => {
              const on = selectedIds.has(p.id)
              return (
                <button key={p.id} className={`filter-opt${on ? ' on' : ''}`}
                        onClick={() => toggle(p.id)} role="checkbox" aria-checked={on}>
                  <span className="box">{on ? '✓' : ''}</span>
                  <span className="opt-name">{p.name}</span>
                  <span className="vcount">{p.vendorCount} vendors</span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
