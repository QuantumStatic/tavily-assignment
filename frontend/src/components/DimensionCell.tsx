import type { CellState } from '../rows'

export function DimensionCell({ dim, state }: { dim: string; state: CellState }) {
  if (state === 'idle') return <td className="cell idle">—</td>
  if (state === 'pending')
    return <td className="cell"><span className="dot" data-testid={`cell-${dim}-pending`} /></td>
  if (state === 'failed')
    return <td className="cell"><span className="failed" title="failed">✗</span></td>
  const band = state.score >= 7 ? 'good' : state.score >= 4 ? 'mid' : 'bad'
  return <td className="cell"><span className={`pill ${band}`}>{state.score}</span></td>
}
