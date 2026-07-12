import { useEffect, useState } from 'react'
import { api } from '../api'
import type { TrendResponse, VendorOption } from '../trend'
import { DIMENSIONS } from '../dimensions'
import { TrendChart } from './TrendChart'
import { MultiSelectDropdown } from './MultiSelectDropdown'

const FEATURES = [{ key: 'verdict', label: 'Verdict' }, ...DIMENSIONS.map((d) => ({ key: d.key, label: d.label }))]

export function TrendPanel() {
  const [options, setOptions] = useState<VendorOption[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [dimension, setDimension] = useState('verdict')
  const [trend, setTrend] = useState<TrendResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getVendorOptions()
      .then(setOptions)
      .catch(() => setError('Could not load the vendor list.'))
  }, [])

  useEffect(() => {
    if (selected.size === 0) { setTrend(null); return }
    let cancelled = false
    api.getTrend([...selected], dimension)
      .then((r) => { if (!cancelled) { setTrend(r); setError(null) } })
      .catch(() => { if (!cancelled) setError('Could not load the score trend.') })
    return () => { cancelled = true }
  }, [selected, dimension])

  const featureLabel = FEATURES.find((f) => f.key === dimension)?.label ?? dimension

  return (
    <div className="chart-card trend-panel">
      <div className="label">Score trend</div>
      <div className="caption">track selected vendors' scores over time</div>

      <div className="trend-controls">
        <MultiSelectDropdown
          triggerLabel={<>Vendors <span className="count">· {selected.size ? `${selected.size} selected` : 'none'}</span></>}
          items={options.map((o) => ({ id: o.vendor_key, name: o.name }))}
          selectedIds={selected}
          onChange={setSelected}
          searchLabel="Filter vendors"
          searchPlaceholder="Filter vendors…"
          emptyText="No vendors found."
        />

        <select className="trend-feature-select" aria-label="Score feature"
                value={dimension} onChange={(e) => setDimension(e.target.value)}>
          {FEATURES.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
      </div>

      {error ? (
        <p className="muted">{error}</p>
      ) : selected.size === 0 ? (
        <p className="muted">Select one or more vendors to see their {featureLabel.toLowerCase()} trend.</p>
      ) : !trend || trend.series.every((s) => s.points.length === 0) ? (
        <p className="muted">No history yet for the selected vendors.</p>
      ) : (
        <TrendChart series={trend.series} />
      )}
    </div>
  )
}
