import type { CellState } from '../rows'
import { bandForScore } from '../band'

export function DimensionCell({ dim, state }: { dim: string; state: CellState }) {
  if (state === 'idle') return <td className="cell idle">—</td>
  if (state === 'pending')
    return (
      <td className="cell">
        <span className="dot" data-testid={`cell-${dim}-pending`} />
        <span className="sr-only">pending</span>
      </td>
    )
  if (state === 'failed')
    return <td className="cell"><span className="failed" title="failed">✗</span></td>
  const band = bandForScore(state.score)
  return <td className="cell"><span className={`pill ${band}`}>{state.score}</span></td>
}
