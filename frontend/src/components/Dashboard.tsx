import type { DashboardStats, VendorRef } from '../dashboard'
import { bandForScore } from '../band'
import { DIMENSIONS } from '../dimensions'

const LABEL = new Map(DIMENSIONS.map((d) => [d.key, d.label]))
const bandColor = (score: number) => `var(--${bandForScore(score)})`

function Histogram({ hist }: { hist: number[] }) {
  const max = Math.max(1, ...hist)
  return (
    <div className="hist">
      {hist.map((n, score) => (
        <div key={score} className="col">
          <span className="count">{n || ''}</span>
          <span className="bar" style={{ height: `${(n / max) * 100}%`, background: bandColor(score) }} />
          <span className="n">{score}</span>
        </div>
      ))}
    </div>
  )
}

export function Dashboard({ stats, onOpenVendor }: {
  stats: DashboardStats
  onOpenVendor: (ref: VendorRef) => void
}) {
  if (stats.projects_selected === 0) {
    return <p className="empty">Select at least one project to see the overview.</p>
  }
  if (stats.vendors_generated === 0) {
    return <p className="empty">No researched vendors yet in the selected projects.</p>
  }
  const s = stats
  const totalSrc = s.independent_sources + s.self_reported_sources
  const indPct = totalSrc ? Math.round((s.independent_sources / totalSrc) * 100) : 0

  return (
    <div className="dashboard">
      <div className="cards">
        <div className="card">
          <div className="label">Portfolio verdict</div>
          <div className="big">{s.avg_verdict?.toFixed(1) ?? '—'} <small>/ 10</small>
            {s.avg_verdict != null && <span className={`pill ${bandForScore(Math.round(s.avg_verdict))}`}>
              {bandForScore(Math.round(s.avg_verdict)) === 'good' ? 'Cleared' : bandForScore(Math.round(s.avg_verdict)) === 'mid' ? 'Watch' : 'High risk'}
            </span>}
          </div>
          <div className="hint">avg across {s.vendors_generated} researched vendors</div>
        </div>
        <div className="card">
          <div className="label">Risk triage</div>
          <div className="triage">
            <span style={{ flex: s.risk_high || 0.001, background: 'var(--bad)' }} />
            <span style={{ flex: s.risk_watch || 0.001, background: 'var(--mid)' }} />
            <span style={{ flex: s.risk_cleared || 0.001, background: 'var(--good)' }} />
          </div>
          <div className="legend">
            <span><i className="swatch" style={{ background: 'var(--bad)' }} /><b>{s.risk_high}</b> high</span>
            <span><i className="swatch" style={{ background: 'var(--mid)' }} /><b>{s.risk_watch}</b> watch</span>
            <span><i className="swatch" style={{ background: 'var(--good)' }} /><b>{s.risk_cleared}</b> cleared</span>
          </div>
        </div>
        <div className="card">
          <div className="label">Evidence sources</div>
          <div className="split">
            <span style={{ flex: s.independent_sources || 0.001, background: 'var(--accent)' }} />
            <span style={{ flex: s.self_reported_sources || 0.001, background: 'var(--bar)' }} />
          </div>
          <div className="legend">
            <span><i className="swatch" style={{ background: 'var(--accent)' }} /><b>{indPct}%</b> independent</span>
            <span><i className="swatch" style={{ background: 'var(--bar)' }} /><b>{100 - indPct}%</b> self-reported</span>
          </div>
        </div>
      </div>

      <div className="decisions">
        <div className="chart-card">
          <div className="label">Shortlist</div>
          <div className="caption">highest verdicts</div>
          <div className="dlist">
            {s.shortlist.map((e, i) => (
              <div key={e.ref.vendor_id} className="drow good" role="button" tabIndex={0}
                   onClick={() => onOpenVendor(e.ref)}
                   onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onOpenVendor(e.ref) } }}>
                <span className="medal">{i + 1}</span>
                <span className="vname">{e.ref.name}</span>
                <span className="why">{e.ref.project_name}</span>
                <span className="score">{e.verdict}</span>
                {e.previous_verdict != null && (
                  <span className="delta">
                    {e.verdict >= e.previous_verdict ? '▲' : '▼'} was {e.previous_verdict}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="chart-card">
          <div className="label">Red flags</div>
          <div className="caption">any dimension ≤3, or weak backlog + financial</div>
          <div className="dlist">
            {s.red_flags.length === 0 && <p className="muted">None — nothing scored ≤3.</p>}
            {s.red_flags.map((f, i) => (
              <div key={i} className="drow bad" role="button" tabIndex={0}
                   onClick={() => onOpenVendor(f.ref)}
                   onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onOpenVendor(f.ref) } }}>
                <span className="vname">{f.ref.name}</span>
                <span className="why">{f.detail}{f.kind === 'delivery_risk' ? ' — delivery risk' : ''}</span>
                <span className="score">{f.score}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="chart-card">
          <div className="label">Most trusted</div>
          <div className="caption">chosen before across your projects</div>
          <div className="dlist">
            {s.most_trusted.length === 0 && <p className="muted">No vendors chosen yet.</p>}
            {s.most_trusted.map((t) => (
              <div key={t.vendor_key} className="drow">
                <span className="vname">{t.name}</span>
                <span className="trust">chosen {t.chosen_count}× · in {t.project_count} projects</span>
                <span className="score">{t.verdict ?? '—'}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="charts">
        <div className="chart-card">
          <div className="label">Verdict distribution</div>
          <div className="caption">every researched vendor's overall score</div>
          <Histogram hist={s.verdict_histogram} />
        </div>
        <div className="chart-card">
          <div className="label">Dimension averages</div>
          <div className="caption">portfolio average per axis — weakest highlighted</div>
          <div className="dims">
            {DIMENSIONS.map((d) => {
              const avg = s.dimension_avgs[d.key]
              const weak = s.weakest_dimension === d.key
              return (
                <div key={d.key} className={`dim${weak ? ' weakest' : ''}`}>
                  <span className="name">{d.label}</span>
                  <span className="track"><span className="fill" style={{ width: `${(avg ?? 0) * 10}%` }} /></span>
                  <span className="val">{avg?.toFixed(1) ?? '—'}</span>
                </div>
              )
            })}
          </div>
          {s.weakest_dimension && (
            <div className="weak-note">
              {LABEL.get(s.weakest_dimension) ?? s.weakest_dimension} is the softest axis — {s.weakest_low_count} vendors score ≤3.
            </div>
          )}
        </div>
      </div>

      <div className="multis">
        {DIMENSIONS.map((d) => (
          <div key={d.key} className={`mini${s.weakest_dimension === d.key ? ' weakest' : ''}`}>
            <div className="head">
              <span className="name">{d.label}</span>
              <span className="avg">avg {s.dimension_avgs[d.key]?.toFixed(1) ?? '—'}</span>
            </div>
            <Histogram hist={s.dimension_histograms[d.key] ?? Array(11).fill(0)} />
          </div>
        ))}
      </div>
    </div>
  )
}
