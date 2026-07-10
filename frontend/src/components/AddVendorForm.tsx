import { useState } from 'react'

export function AddVendorForm({ onAdd }: { onAdd: (name: string) => void }) {
  const [name, setName] = useState('')
  return (
    <form
      className="add-vendor"
      onSubmit={(e) => {
        e.preventDefault()
        const trimmed = name.trim()
        if (!trimmed) return
        onAdd(trimmed)
        setName('')
      }}
    >
      <label htmlFor="add-vendor-name" className="sr-only">Vendor name</label>
      <input
        id="add-vendor-name"
        placeholder="Vendor name…"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <button type="submit">+ Add vendor</button>
    </form>
  )
}
