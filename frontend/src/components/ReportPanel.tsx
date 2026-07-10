import { useState } from 'react'
import type { RowState } from '../rows'
import { DIMENSIONS } from '../dimensions'
import { bandForScore } from '../band'

const LABEL = new Map(DIMENSIONS.map((d) => [d.key, d.label]))

const DEFAULT_WIDTH = 360
const MIN_WIDTH = 280
const MAX_WIDTH = 720
const KEYBOARD_STEP = 20

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

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
  const [width, setWidth] = useState(DEFAULT_WIDTH)

  function startDrag(e: React.MouseEvent) {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = width
    function onMove(ev: MouseEvent) {
      // dragging the left edge leftward should widen the panel
      setWidth(clamp(startWidth + (startX - ev.clientX), MIN_WIDTH, MAX_WIDTH))
    }
    function onUp() {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  function onHandleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowLeft') {
      setWidth((w) => clamp(w + KEYBOARD_STEP, MIN_WIDTH, MAX_WIDTH))
      e.preventDefault()
    } else if (e.key === 'ArrowRight') {
      setWidth((w) => clamp(w - KEYBOARD_STEP, MIN_WIDTH, MAX_WIDTH))
      e.preventDefault()
    }
  }

  return (
    <aside className="panel" style={{ width }}>
      <div
        className="panel-resize-handle"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize report panel"
        aria-valuenow={width}
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        tabIndex={0}
        onMouseDown={startDrag}
        onKeyDown={onHandleKeyDown}
      />
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

      {!report && row.status === 'done' && (
        <p className="muted">
          {row.sectionsPresent != null && row.sectionsExpected != null
            ? `Still generating — ${row.sectionsPresent} of ${row.sectionsExpected} dimensions complete.`
            : 'Report data is incomplete.'}
        </p>
      )}

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
