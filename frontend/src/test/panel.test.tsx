import { expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
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

test('a done row with no report yet (partial completeness) shows a still-generating note, not a blank body', () => {
  render(
    <ReportPanel
      row={{ ...row, status: 'done', report: undefined, sectionsPresent: 2, sectionsExpected: 7 }}
      onClose={() => {}}
    />,
  )
  expect(screen.getByText(/still generating.*2 of 7 dimensions complete/i)).toBeInTheDocument()
})

test('omits the "as of" suffix when a citation has no date', () => {
  const noDate = {
    ...row,
    report: {
      ...row.report!,
      sections: [{
        dimension: 'legal', score: 8, reasoning: 'clean',
        findings: [{ claim: 'No litigation.',
                     citation: { url: 'https://pacer.gov', title: 'PACER', source_type: 'independent', score: 0.9, as_of: null } }],
      }],
    },
  }
  render(<ReportPanel row={noDate} onClose={() => {}} />)
  expect(screen.queryByText(/as of/i)).not.toBeInTheDocument()
  expect(screen.queryByText(/n\/a/i)).not.toBeInTheDocument()
  expect(screen.getByText('independent')).toBeInTheDocument()
})

test('renders a safe https citation link', () => {
  render(<ReportPanel row={row} onClose={() => {}} />)
  expect(screen.getByRole('link', { name: /pacer/i })).toHaveAttribute('href', 'https://pacer.gov')
})

test('does not render an anchor for an unsafe URL scheme', () => {
  const unsafeRow = {
    ...row,
    report: {
      ...row.report!,
      sections: [{
        dimension: 'legal', score: 8, reasoning: 'clean',
        findings: [{ claim: 'Bad link.',
                     citation: { url: 'javascript:alert(1)', title: 'Evil', source_type: 'independent', score: 0.9, as_of: '2026-06' } }],
      }],
    },
  }
  render(<ReportPanel row={unsafeRow} onClose={() => {}} />)
  expect(screen.queryByRole('link', { name: /evil/i })).not.toBeInTheDocument()
  expect(screen.getByText(/evil/i)).toBeInTheDocument()
})

test('verdict pill uses the score band, not always green', () => {
  render(<ReportPanel row={{ ...row, verdict: { score: 2 } }} onClose={() => {}} />)
  const pill = screen.getByText('2/10')
  expect(pill.className).toMatch(/\bbad\b/)
})

test('the panel starts at the default width and exposes a resize handle', () => {
  render(<ReportPanel row={row} onClose={() => {}} />)
  const panel = screen.getByRole('separator', { name: /resize report panel/i }).closest('.panel')
  expect(panel).toHaveStyle({ width: '360px' })
})

test('dragging the resize handle left widens the panel', () => {
  render(<ReportPanel row={row} onClose={() => {}} />)
  const handle = screen.getByRole('separator', { name: /resize report panel/i })
  const panel = handle.closest('.panel')!

  fireEvent.mouseDown(handle, { clientX: 500 })
  fireEvent.mouseMove(window, { clientX: 400 })   // dragged 100px left
  expect(panel).toHaveStyle({ width: '460px' })

  fireEvent.mouseUp(window)
  fireEvent.mouseMove(window, { clientX: 200 })   // no listener anymore -> no further change
  expect(panel).toHaveStyle({ width: '460px' })
})

test('resize is clamped to the min/max width bounds', () => {
  render(<ReportPanel row={row} onClose={() => {}} />)
  const handle = screen.getByRole('separator', { name: /resize report panel/i })
  const panel = handle.closest('.panel')!

  fireEvent.mouseDown(handle, { clientX: 500 })
  fireEvent.mouseMove(window, { clientX: 5000 })  // drag far right -> would shrink below MIN_WIDTH
  expect(panel).toHaveStyle({ width: '280px' })
  fireEvent.mouseUp(window)

  fireEvent.mouseDown(handle, { clientX: 500 })
  fireEvent.mouseMove(window, { clientX: -5000 }) // drag far left -> would exceed MAX_WIDTH
  expect(panel).toHaveStyle({ width: '720px' })
  fireEvent.mouseUp(window)
})

test('arrow keys on the resize handle adjust the panel width', () => {
  render(<ReportPanel row={row} onClose={() => {}} />)
  const handle = screen.getByRole('separator', { name: /resize report panel/i })
  const panel = handle.closest('.panel')!

  fireEvent.keyDown(handle, { key: 'ArrowLeft' })
  expect(panel).toHaveStyle({ width: '380px' })

  fireEvent.keyDown(handle, { key: 'ArrowRight' })
  fireEvent.keyDown(handle, { key: 'ArrowRight' })
  expect(panel).toHaveStyle({ width: '340px' })
})
