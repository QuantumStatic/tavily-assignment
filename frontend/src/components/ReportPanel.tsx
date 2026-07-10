import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { bandForScore } from '../band'

const LABEL = new Map(DIMENSIONS.map((d) => [d.key, d.label]))

function isSafeUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

export function ReportPanel({ row, onClose }: { row: RowState; onClose: () => void }) {
  const report = row.report
  const entity = row.entity
  const verdict = row.verdict

  return (
    <aside className="panel">
      <div className="panel-head">
        <div>
          <strong>{entity?.name ?? row.name}</strong>
          {typeof verdict === 'object' && (
            <span className={`pill ${bandForScore(verdict.score)}`}> {verdict.score}/10</span>
          )}
        </div>
        <button className="link-btn" onClick={onClose} aria-label="Close">✕</button>
      </div>

      {row.status === 'error' && <p className="failed">Report failed: {row.errorMsg}</p>}

      {!report && row.status === 'streaming' && <p className="muted">Generating report…</p>}

      {report && (
        <>
          <p className="verdict-reason">{report.verdict_reasoning}</p>
          {report.sections.map((s) => (
            <section key={s.dimension} className="report-section">
              <h5>{LABEL.get(s.dimension) ?? s.dimension} · {s.score}/10</h5>
              <p className="muted">{s.reasoning}</p>
              {s.findings.map((f, i) => (
                <div key={i} className="finding">
                  <span>{f.claim}</span>
                  {isSafeUrl(f.citation.url) ? (
                    <a href={f.citation.url} target="_blank" rel="noopener noreferrer">
                      ↗ {f.citation.title}
                    </a>
                  ) : (
                    <span className="cite-meta">{f.citation.title} (link unavailable)</span>
                  )}
                  <span className="cite-meta">
                    {f.citation.source_type} · as of {f.citation.as_of ?? 'n/a'}
                  </span>
                </div>
              ))}
            </section>
          ))}
        </>
      )}
    </aside>
  )
}
