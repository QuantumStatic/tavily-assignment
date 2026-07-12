import type { TrendSeries } from '../trend'

const COLORS = ['var(--accent)', '#22c55e', '#eab308', '#ef4444', '#a855f7', '#06b6d4', '#f97316', '#ec4899']

const W = 640
const H = 200
const PAD_L = 26
const PAD_R = 12
const PAD_T = 12
const PAD_B = 26

function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number)
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short', year: '2-digit' })
}

export function TrendChart({ series }: { series: TrendSeries[] }) {
  const months = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.month)))).sort()
  const innerW = W - PAD_L - PAD_R
  const innerH = H - PAD_T - PAD_B
  const xFor = (i: number) => (months.length <= 1 ? PAD_L + innerW / 2 : PAD_L + (i / (months.length - 1)) * innerW)
  const yFor = (score: number) => PAD_T + innerH - (score / 10) * innerH

  return (
    <div className="trend-chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="trend-chart" role="img" aria-label="Score trend over time">
        {[0, 5, 10].map((v) => (
          <line key={v} x1={PAD_L} x2={W - PAD_R} y1={yFor(v)} y2={yFor(v)} className="trend-grid" />
        ))}
        {[0, 5, 10].map((v) => (
          <text key={v} x={PAD_L - 6} y={yFor(v) + 3} className="trend-axis-label" textAnchor="end">{v}</text>
        ))}
        {series.map((s, si) => {
          const color = COLORS[si % COLORS.length]
          const byMonth = new Map(s.points.map((p) => [p.month, p.score]))
          const coords = months
            .map((m, i) => (byMonth.has(m) ? [xFor(i), yFor(byMonth.get(m)!)] as const : null))
            .filter((c): c is readonly [number, number] => c !== null)
          const path = coords.map((c) => c.join(',')).join(' ')
          return (
            <g key={s.vendor_key}>
              {coords.length > 1 && <polyline points={path} fill="none" stroke={color} strokeWidth="2" />}
              {coords.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="3" fill={color} />)}
            </g>
          )
        })}
        {months.map((m, i) => (
          <text key={m} x={xFor(i)} y={H - 6} className="trend-axis-label" textAnchor="middle">{monthLabel(m)}</text>
        ))}
      </svg>
      <div className="trend-legend">
        {series.map((s, si) => (
          <span key={s.vendor_key} className="trend-legend-item">
            <i className="swatch" style={{ background: COLORS[si % COLORS.length] }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  )
}
