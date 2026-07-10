import type { Project } from '../types'
import { NewProjectForm } from './NewProjectForm'

export function Sidebar({
  projects, activeId, onSelect, onCreate,
}: {
  projects: Project[]
  activeId: number | null
  onSelect: (id: number) => void
  onCreate: (name: string) => void
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
              {p.name}
            </li>
          ))}
        </ul>
      )}
      <NewProjectForm onCreate={onCreate} />
    </aside>
  )
}
