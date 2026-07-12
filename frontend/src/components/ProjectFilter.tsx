import { MultiSelectDropdown } from './MultiSelectDropdown'

export interface FilterProject { id: number; name: string; vendorCount: number }

export function ProjectFilter({
  projects, selectedIds, onChange,
}: {
  projects: FilterProject[]
  selectedIds: Set<number>
  onChange: (ids: Set<number>) => void
}) {
  const total = projects.length
  const sel = selectedIds.size
  const summary = sel === total ? `all (${total})` : `${sel} of ${total}`

  return (
    <MultiSelectDropdown
      triggerLabel={<>Projects <span className="count">· {summary}</span></>}
      items={projects.map((p) => ({ id: p.id, name: p.name, meta: `${p.vendorCount} vendors` }))}
      selectedIds={selectedIds}
      onChange={onChange}
      searchLabel="Filter projects"
      searchPlaceholder="Filter projects…"
    />
  )
}
