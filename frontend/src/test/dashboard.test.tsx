import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Dashboard } from '../components/Dashboard'
import type { DashboardStats } from '../dashboard'

const stats: DashboardStats = {
  projects_total: 3, projects_selected: 3, vendors_total: 14, vendors_generated: 12,
  avg_verdict: 6.4, risk_high: 2, risk_watch: 4, risk_cleared: 6,
  independent_sources: 71, self_reported_sources: 29,
  verdict_histogram: [0, 0, 1, 1, 0, 2, 2, 4, 1, 1, 0],
  dimension_avgs: { legal: 6.8, safety: 4.1, financial: 6.3, backlog: 7.2, certifications: 7.7, news: 6.6 },
  dimension_histograms: {
    legal: [0, 0, 0, 1, 0, 1, 2, 3, 2, 1, 0], safety: [0, 1, 2, 3, 2, 2, 1, 1, 0, 0, 0],
    financial: [0, 0, 1, 0, 1, 2, 3, 2, 2, 0, 0], backlog: [0, 0, 0, 0, 1, 1, 2, 3, 3, 1, 0],
    certifications: [0, 0, 0, 0, 0, 1, 1, 2, 3, 2, 0], news: [0, 0, 1, 0, 1, 1, 2, 3, 2, 1, 0],
  },
  weakest_dimension: 'safety', weakest_low_count: 3,
  shortlist: [{ ref: { vendor_id: 1, name: 'Bechtel', project_id: 1, project_name: 'Bridge job' }, verdict: 9, previous_verdict: 8 }],
  red_flags: [{ ref: { vendor_id: 2, name: 'Fluor', project_id: 1, project_name: 'Bridge job' }, kind: 'dimension', dimension: 'safety', score: 2, detail: 'Safety 2' }],
  most_trusted: [{ name: 'Bechtel', vendor_key: 'bechtel.com', chosen_count: 4, project_count: 5, verdict: 9 }],
}

test('renders the headline verdict and coverage', () => {
  render(<Dashboard stats={stats} onOpenVendor={() => {}} />)
  expect(screen.getByText('6.4')).toBeInTheDocument()
  expect(screen.getByText(/12 of 14/)).toBeInTheDocument()
})

test('renders decisions: shortlist, red flags, most trusted', () => {
  render(<Dashboard stats={stats} onOpenVendor={() => {}} />)
  // 'Bechtel' appears twice by design: once in the shortlist, once in most-trusted.
  expect(screen.getAllByText('Bechtel').length).toBe(2)
  expect(screen.getByText('Fluor')).toBeInTheDocument()
  expect(screen.getByText(/chosen 4/i)).toBeInTheDocument()
})

test('names the weakest dimension', () => {
  render(<Dashboard stats={stats} onOpenVendor={() => {}} />)
  expect(screen.getByText(/is the softest axis/i)).toBeInTheDocument()
})

test('renders a delta marker on shortlist entries with a previous verdict', () => {
  render(<Dashboard stats={stats} onOpenVendor={() => {}} />)
  expect(screen.getByText(/▲1/)).toBeInTheDocument()
})

test('empty-selection state when nothing selected', () => {
  const empty = { ...stats, projects_selected: 0, vendors_total: 0, vendors_generated: 0, avg_verdict: null }
  render(<Dashboard stats={empty} onOpenVendor={() => {}} />)
  expect(screen.getByText(/select at least one project/i)).toBeInTheDocument()
})
