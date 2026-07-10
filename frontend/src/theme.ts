import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'vendor-dd-theme'

function prefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

// localStorage may be absent or throw (private-browsing modes, or a bare stub in
// some test environments) — never let a theme preference read/write break the app.
function readStored(): string | null {
  try {
    return typeof localStorage?.getItem === 'function' ? localStorage.getItem(STORAGE_KEY) : null
  } catch {
    return null
  }
}

function writeStored(theme: Theme): void {
  try {
    if (typeof localStorage?.setItem === 'function') localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* ignore — persistence is best-effort */
  }
}

function initialTheme(): Theme {
  const stored = readStored()
  if (stored === 'light' || stored === 'dark') return stored
  return prefersDark() ? 'dark' : 'light'
}

/**
 * Theme state persisted to localStorage and reflected as `data-theme` on the
 * document root (styles.css keys its dark palette off `:root[data-theme="dark"]`).
 * Falls back to the OS `prefers-color-scheme` on first visit.
 */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    writeStored(theme)
  }, [theme])

  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))
  return [theme, toggle]
}
