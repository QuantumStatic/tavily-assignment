import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VendorTable } from '../components/VendorTable'
import { rowFromSummary } from '../rows'
import type { VendorSummary } from '../types'

const summary = (over: Partial<VendorSummary>): VendorSummary => ({
  vendor_id: 1, name: 'Cives', vendor_key: 'cives.com', generated: false,
  sections_present: 0, sections_expected: 6,
  verdict_score: null, verdict_reasoning: null, dimensions: [], ...over,
})

test('renders a header per dimension plus vendor + verdict', () => {
  render(<VendorTable rows={[]} onSelect={() => {}} onDelete={() => {}} />)
  expect(screen.getByText('Legal')).toBeInTheDocument()
  expect(screen.getByText('News')).toBeInTheDocument()
  expect(screen.getByText('Verdict')).toBeInTheDocument()
})

test('each dimension header has a help badge explaining it and the score direction', () => {
  render(<VendorTable rows={[]} onSelect={() => {}} onDelete={() => {}} />)
  const legalHelp = screen.getByLabelText(/litigation, lawsuits/i)
  // the tooltip text is rendered in the DOM (CSS shows it on hover/focus)
  expect(legalHelp).toHaveTextContent(/10 = clean record; 0 = serious/i)
  // verdict column explains the overall score direction too
  expect(screen.getByLabelText(/overall due-diligence score/i)).toBeInTheDocument()
})

test('renders a scored row and fires onSelect on click', async () => {
  const row = rowFromSummary(summary({
    generated: true, verdict_score: 7,
    dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
  }))
  const onSelect = vi.fn()
  render(<VendorTable rows={[row]} onSelect={onSelect} onDelete={() => {}} />)
  expect(screen.getByText('Cives')).toBeInTheDocument()
  expect(screen.getByText('8')).toBeInTheDocument()
  await userEvent.click(screen.getByText('Cives'))
  expect(onSelect).toHaveBeenCalledWith(1)
})

test('a streaming row shows a pending indicator', () => {
  const row = { ...rowFromSummary(summary({})), status: 'streaming' as const,
                cells: { legal: 'pending' as const }, verdict: 'pending' as const }
  render(<VendorTable rows={[row]} onSelect={() => {}} onDelete={() => {}} />)
  expect(screen.getByTestId('cell-legal-pending')).toBeInTheDocument()
  // screen readers get a text label, not just a silent pulsing dot
  expect(screen.getAllByText('pending').length).toBeGreaterThan(0)
})

test('the delete button has an accessible name', () => {
  const row = rowFromSummary(summary({ generated: true, verdict_score: 7, dimensions: [] }))
  render(<VendorTable rows={[row]} onSelect={() => {}} onDelete={() => {}} />)
  expect(screen.getByRole('button', { name: /delete vendor/i })).toBeInTheDocument()
})

test('pressing Enter on a focused row selects it', async () => {
  const row = rowFromSummary(summary({ generated: true, verdict_score: 7, dimensions: [] }))
  const onSelect = vi.fn()
  render(<VendorTable rows={[row]} onSelect={onSelect} onDelete={() => {}} />)
  screen.getByText('Cives').closest('tr')!.focus()
  await userEvent.keyboard('{Enter}')
  expect(onSelect).toHaveBeenCalledWith(1)
})
