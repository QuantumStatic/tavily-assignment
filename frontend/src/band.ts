export type ScoreBand = 'good' | 'mid' | 'bad'

export function bandForScore(score: number): ScoreBand {
  return score >= 7 ? 'good' : score >= 4 ? 'mid' : 'bad'
}
