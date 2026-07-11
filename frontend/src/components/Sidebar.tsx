import { useState } from 'react'
import type { Project } from '../types'

export function Sidebar({
  projects, activeId, onSelect, onCreate, onDelete,
}: {
  projects: Project[]
  activeId: number | null
  onSelect: (id: number) => void
  onCreate: (name: string) => void
  onDelete: (id: number) => void
}) {
  const [query, setQuery] = useState('')
  const q = query.trim()
  const ql = q.toLowerCase()
  const filtered = q ? projects.filter((p) => p.name.toLowerCase().includes(ql)) : projects
  // offer to create only when the query matches no existing project at all
  const showCreate = q !== '' && filtered.length === 0

  const create = () => {
    if (!showCreate) return
    onCreate(q)
    setQuery('')
  }

  return (
    <aside className="sidebar">
      <h4>Projects</h4>
      <input
        className="project-search"
        type="search"
        aria-label="Search or create a project"
        placeholder="Search projects…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return
          e.preventDefault()
          if (filtered.length === 1) onSelect(filtered[0].id)   // jump to the sole match
          else if (showCreate) create()                          // no matches -> create it
        }}
      />

      {filtered.length > 0 && (
        <ul className="project-list">
          {filtered.map((p) => (
            <li
              key={p.id}
              className={p.id === activeId ? 'active' : ''}
              tabIndex={0}
              role="button"
              aria-pressed={p.id === activeId}
              onClick={() => onSelect(p.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(p.id) }
              }}
            >
              <span className="project-name">{p.name}</span>
              <button
                className="delete-btn project-delete"
                aria-label={`Delete project ${p.name}`}
                title="Delete project"
                onClick={(e) => { e.stopPropagation(); onDelete(p.id) }}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
                     stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                     strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 6h18" />
                  <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                  <path d="M10 11v6" />
                  <path d="M14 11v6" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}

      {showCreate && (
        <button className="project-create" onClick={create}>
          + Create “{q}”
        </button>
      )}

      {projects.length === 0 && q === '' && (
        <p className="empty">Create a project to begin.</p>
      )}
    </aside>
  )
}
