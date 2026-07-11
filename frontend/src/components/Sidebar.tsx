import type { Project } from '../types'
import { NewProjectForm } from './NewProjectForm'

export function Sidebar({
  projects, activeId, onSelect, onCreate, onDelete,
}: {
  projects: Project[]
  activeId: number | null
  onSelect: (id: number) => void
  onCreate: (name: string) => void
  onDelete: (id: number) => void
}) {
  return (
    <aside className="sidebar">
      <h4>Projects</h4>
      {projects.length === 0 ? (
        <p className="empty">Create a project to begin.</p>
      ) : (
        <ul className="project-list">
          {projects.map((p) => (
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
      <NewProjectForm onCreate={onCreate} />
    </aside>
  )
}
