import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import { FakeEventSource } from './fakeEventSource'

function mockApi() {
  const project = { id: 1, name: 'Bridge job', created_at: 't' }
  const detail = { id: 1, name: 'Bridge job', created_at: 't', vendors: [] as unknown[] }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = init?.method ?? 'GET'
    if (u.endsWith('/projects') && method === 'GET')
      return { ok: true, json: async () => [project] }
    if (u.endsWith('/projects') && method === 'POST')
      return { ok: true, json: async () => project }
    if (u.match(/\/projects\/1$/))
      return { ok: true, json: async () => detail }
    if (u.match(/\/projects\/1\/vendors$/) && method === 'POST')
      return { ok: true, json: async () => ({ id: 5, project_id: 1, name: 'Cives Steel', vendor_key: null, created_at: 't' }) }
    return { ok: true, json: async () => ({}) }
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock }
}

beforeEach(() => FakeEventSource.reset())
afterEach(() => vi.restoreAllMocks())

test('add a vendor, watch cells stream in, open the report panel', async () => {
  mockApi()
  render(<App />)

  // project loads into the sidebar and auto-selects
  await screen.findByText('Bridge job')

  // add a vendor -> POST then a stream opens
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  const row = await screen.findByText('Cives Steel')

  // drive the stream
  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  es.emit('entity_resolved', { entity: { name: 'Cives Steel', domain: 'cives.com' } })
  es.emit('section_complete', { section: { dimension: 'legal', score: 8, findings: [], reasoning: 'clean' }, cached: false })
  es.emit('report_complete', {
    report: {
      vendor_input: 'Cives Steel', verdict_score: 7, verdict_reasoning: 'Solid.',
      entity: { name: 'Cives Steel' },
      sections: [{ dimension: 'legal', score: 8, reasoning: 'clean',
                   findings: [{ claim: 'No litigation.', citation: { url: 'https://x.com', title: 'X', source_type: 'independent', score: 0.9, as_of: '2026-06' } }] }],
    },
  })

  // the verdict cell filled in
  await waitFor(() => expect(screen.getByText('7/10')).toBeInTheDocument())

  // click the row -> panel shows findings
  await userEvent.click(row)
  await screen.findByText('No litigation.')
  expect(screen.getByRole('link', { name: /X/ })).toHaveAttribute('href', 'https://x.com')
})
