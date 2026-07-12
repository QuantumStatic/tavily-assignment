import { afterEach, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TrendPanel } from '../components/TrendPanel'

const OPTIONS = [
  { vendor_key: 'fluor.com', name: 'Fluor Corporation' },
  { vendor_key: 'siemens-energy.com', name: 'Siemens Energy' },
]

function mockApi(trendByDimension: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (url: string) => {
    const u = String(url)
    if (u.match(/\/vendor-options(\?|$)/)) return { ok: true, json: async () => OPTIONS }
    if (u.match(/\/trend(\?|$)/)) {
      const dim = new URL(u, 'http://x').searchParams.get('dimension') ?? 'verdict'
      const keys = (new URL(u, 'http://x').searchParams.get('vendor_keys') ?? '').split(',').filter(Boolean)
      const body = trendByDimension[dim] ?? {
        dimension: dim,
        series: keys.map((k) => ({
          vendor_key: k, name: OPTIONS.find((o) => o.vendor_key === k)?.name ?? k,
          points: [{ month: '2026-07', score: 6 }],
        })),
      }
      return { ok: true, json: async () => body }
    }
    return { ok: true, json: async () => ({}) }
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock }
}

afterEach(() => vi.restoreAllMocks())

async function openPicker() {
  await userEvent.click(screen.getByRole('button', { name: /vendors/i }))
}

test('shows an empty prompt when no vendors are selected', async () => {
  mockApi()
  render(<TrendPanel />)
  expect(await screen.findByText(/select one or more vendors/i)).toBeInTheDocument()
})

test('opening the picker lists fetched vendor options', async () => {
  mockApi()
  render(<TrendPanel />)
  await openPicker()
  expect(await screen.findByText('Fluor Corporation')).toBeInTheDocument()
  expect(screen.getByText('Siemens Energy')).toBeInTheDocument()
})

test('search filters the vendor options', async () => {
  mockApi()
  render(<TrendPanel />)
  await openPicker()
  await screen.findByText('Fluor Corporation')
  await userEvent.type(screen.getByRole('searchbox', { name: /filter vendors/i }), 'siemens')
  expect(screen.getByText('Siemens Energy')).toBeInTheDocument()
  expect(screen.queryByText('Fluor Corporation')).toBeNull()
})

test('selecting a vendor fetches and renders its trend', async () => {
  mockApi()
  render(<TrendPanel />)
  await openPicker()
  await userEvent.click(await screen.findByText('Fluor Corporation'))
  // "Fluor Corporation" now appears twice: the still-open dropdown option, and the chart legend
  await screen.findAllByText('Fluor Corporation')
  expect(document.querySelector('.trend-chart')).toBeInTheDocument()
  expect(document.querySelector('.trend-legend-item')).toBeInTheDocument()
})

test('select all then deselect all toggles the whole vendor set', async () => {
  const { fetchMock } = mockApi()
  render(<TrendPanel />)
  await openPicker()
  await screen.findByText('Fluor Corporation')
  await userEvent.click(screen.getByRole('button', { name: /^select all$/i }))
  await screen.findByText(/2 selected/i)
  await userEvent.click(screen.getByRole('button', { name: /^deselect all$/i }))
  expect(screen.getByText(/select one or more vendors/i)).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/trend'))).toBe(true)
})

test('changing the feature select re-fetches the trend for the new dimension', async () => {
  const { fetchMock } = mockApi()
  render(<TrendPanel />)
  await openPicker()
  await userEvent.click(await screen.findByText('Fluor Corporation'))
  await screen.findByText(/1 selected/i)

  await userEvent.selectOptions(screen.getByRole('combobox', { name: /score feature/i }), 'legal')
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('dimension=legal'))).toBe(true)
})

test('shows a no-history message when the selected vendor has no points yet', async () => {
  mockApi({ verdict: { dimension: 'verdict', series: [{ vendor_key: 'fluor.com', name: 'Fluor Corporation', points: [] }] } })
  render(<TrendPanel />)
  await openPicker()
  await userEvent.click(await screen.findByText('Fluor Corporation'))
  expect(await screen.findByText(/no history yet/i)).toBeInTheDocument()
})

test('clicking outside the vendor picker closes it', async () => {
  mockApi()
  render(<TrendPanel />)
  await openPicker()
  await screen.findByText('Fluor Corporation')
  await userEvent.click(document.body)
  expect(screen.queryByText('Fluor Corporation')).toBeNull()
})

test('clicking inside the vendor picker does not close it', async () => {
  mockApi()
  render(<TrendPanel />)
  await openPicker()
  await screen.findByText('Fluor Corporation')
  await userEvent.click(screen.getByRole('searchbox', { name: /filter vendors/i }))
  expect(screen.getByText('Fluor Corporation')).toBeInTheDocument()
})
