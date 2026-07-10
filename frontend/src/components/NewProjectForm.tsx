import { useState } from 'react'

export function NewProjectForm({ onCreate }: { onCreate: (name: string) => void }) {
  const [name, setName] = useState('')
  return (
    <form
      className="new-project"
      onSubmit={(e) => {
        e.preventDefault()
        const trimmed = name.trim()
        if (!trimmed) return
        onCreate(trimmed)
        setName('')
      }}
    >
      <label htmlFor="new-project-name" className="sr-only">New project name</label>
      <input
        id="new-project-name"
        placeholder="New project…"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
    </form>
  )
}
