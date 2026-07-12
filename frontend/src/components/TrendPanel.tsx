import { useEffect, useState } from 'react'
import { api } from '../api'
import { filterByName } from '../filter'
import type { TrendResponse, VendorOption } from '../trend'
import { DIMENSIONS } from '../dimensions'
import { TrendChart } from './TrendChart'

const FEATURES = [{ key: 'verdict', label: 'Verdict' }, ...DIMENSIONS.map((d) => ({ key: d.key, label: d.label }))]

export function TrendPanel() {
  const [options, setOptions] = useState<VendorOption[]>([])
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [dimension, setDimension] = useState('verdict')
  const [trend, setTrend] = useState<TrendResponse | null>(null)

  useEffect(() => { api.getVendorOptions().then(setOptions).catch(() => {}) }, [])

  useEffect(() => {
    if (selected.size === 0) { setTrend(null); return }
    let cancelled = false
    api.getTrend([...selected], dimension).then((r) => { if (!cancelled) setTrend(r) }).catch(() => {})
    return () => { cancelled = true }
  }, [selected, dimension])

  const visible = filterByName(options.map((o) => ({ ...o, name: o.name })), query)
  const featureLabel = FEATURES.find((f) => f.key === dimension)?.label ?? dimension

  const toggle = (key: string) => {
    const next = new Set(selected)
    next.has(key) ? next.delete(key) : next.add(key)
    setSelected(next)
  }

  return (
    <div className="chart-card trend-panel">
      <div className="label">Score trend</div>
      <div className="caption">track selected vendors' scores over time</div>

      <div className="trend-controls">
        <div className="project-filter">
          <button className="filter-trigger" onClick={() => setOpen((o) => !o)}
                  aria-haspopup="true" aria-expanded={open}>
            Vendors <span className="count">· {selected.size ? `${selected.size} selected` : 'none'}</span> <span className="caret">▾</span>
          </button>
          {open && (
            <div className="filter-dropdown">
              <input
                className="filter-search" type="search" aria-label="Filter vendors"
                placeholder="Filter vendors…"
                value={query} onChange={(e) => setQuery(e.target.value)}
              />
              <div className="filter-actions">
                <button className="link" onClick={() => setSelected(new Set(options.map((o) => o.vendor_key)))}>
                  Select all
                </button>
                <button className="link" onClick={() => setSelected(new Set())}>Deselect all</button>
              </div>
              <div className="filter-options">
                {visible.length === 0 && <p className="muted trend-empty-options">No vendors found.</p>}
                {visible.map((o) => {
                  const on = selected.has(o.vendor_key)
                  return (
                    <button key={o.vendor_key} className={`filter-opt${on ? ' on' : ''}`}
                            onClick={() => toggle(o.vendor_key)} role="checkbox" aria-checked={on}>
                      <span className="box">{on ? '✓' : ''}</span>
                      <span className="opt-name">{o.name}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        <select className="trend-feature-select" aria-label="Score feature"
                value={dimension} onChange={(e) => setDimension(e.target.value)}>
          {FEATURES.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
      </div>

      {selected.size === 0 ? (
        <p className="muted">Select one or more vendors to see their {featureLabel.toLowerCase()} trend.</p>
      ) : !trend || trend.series.every((s) => s.points.length === 0) ? (
        <p className="muted">No history yet for the selected vendors.</p>
      ) : (
        <TrendChart series={trend.series} />
      )}
    </div>
  )
}
