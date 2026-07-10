import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReportPanel } from '../components/ReportPanel'
import type { RowState } from '../rows'

const row: RowState = {
  vendorId: 1, name: 'Cives', vendorKey: 'cives.com',
  cells: {}, verdict: { score: 7 }, status: 'done',
  entity: { name: 'Cives Steel', domain: 'cives.com', country: 'us', industry: 'steel',
            parent: null, is_public: false, ticker: null, exchange: null },
  report: {
    vendor_input: 'Cives', verdict_score: 7, verdict_reasoning: 'Solid overall.',
    entity: { name: 'Cives Steel' } as never,
    sections: [{
      dimension: 'legal', score: 8, reasoning: 'clean',
      findings: [{ claim: 'No active litigation.',
                   citation: { url: 'https://pacer.gov', title: 'PACER', source_type: 'independent', score: 0.9, as_of: '2026-06' } }],
    }],
  },
}

test('renders verdict, findings and a citation link', () => {
  render(<ReportPanel row={row} onClose={() => {}} />)
  expect(screen.getByText(/Cives Steel/)).toBeInTheDocument()
  expect(screen.getByText(/Solid overall/)).toBeInTheDocument()
  expect(screen.getByText('No active litigation.')).toBeInTheDocument()
  const link = screen.getByRole('link', { name: /pacer/i })
  expect(link).toHaveAttribute('href', 'https://pacer.gov')
  expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
})

test('close button fires onClose', async () => {
  const onClose = vi.fn()
  render(<ReportPanel row={row} onClose={onClose} />)
  await userEvent.click(screen.getByRole('button', { name: /close/i }))
  expect(onClose).toHaveBeenCalledOnce()
})

test('a streaming row with no report yet shows a progress note', () => {
  render(<ReportPanel row={{ ...row, status: 'streaming', report: undefined }} onClose={() => {}} />)
  expect(screen.getByText(/generating/i)).toBeInTheDocument()
})
