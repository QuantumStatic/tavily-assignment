import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import { FakeEventSource } from './fakeEventSource'

type MockApiOptions = {
  projects?: { id: number; name: string; created_at: string }[]
  projectDetails?: Record<number, { id: number; name: string; created_at: string; vendors: unknown[] }>
  vendorReports?: Record<number, unknown>
  deleteVendor?: (id: number) => { ok: boolean; status?: number; json: () => Promise<unknown> }
  addVendor?: () => Promise<{ ok: boolean; json: () => Promise<unknown> }>
}

function mockApi(opts: MockApiOptions = {}) {
  const project = { id: 1, name: 'Bridge job', created_at: 't' }
  const projects = opts.projects ?? [project]
  const defaultDetail = { id: 1, name: 'Bridge job', created_at: 't', vendors: [] as unknown[] }
  const projectDetails = opts.projectDetails ?? { 1: defaultDetail }
  const vendorReports = opts.vendorReports ?? {}
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url)
    const method = init?.method ?? 'GET'
    if (u.endsWith('/projects') && method === 'GET')
      return { ok: true, json: async () => projects }
    if (u.endsWith('/projects') && method === 'POST')
      return { ok: true, json: async () => project }
    const projectMatch = u.match(/\/projects\/(\d+)$/)
    if (projectMatch && method === 'GET') {
      const id = Number(projectMatch[1])
      const detail = projectDetails[id]
      if (detail) return { ok: true, json: async () => detail }
    }
    const addVendorMatch = u.match(/\/projects\/(\d+)\/vendors$/)
    if (addVendorMatch && method === 'POST') {
      if (opts.addVendor) return opts.addVendor()
      return { ok: true, json: async () => ({ id: 5, project_id: Number(addVendorMatch[1]), name: 'Cives Steel', vendor_key: null, created_at: 't' }) }
    }
    const reportMatch = u.match(/\/vendors\/(\d+)\/report$/)
    if (reportMatch && method === 'GET') {
      const id = Number(reportMatch[1])
      const report = vendorReports[id]
      if (report) return { ok: true, json: async () => report }
    }
    const deleteMatch = u.match(/\/vendors\/(\d+)$/)
    if (deleteMatch && method === 'DELETE') {
      const id = Number(deleteMatch[1])
      if (opts.deleteVendor) return opts.deleteVendor(id)
      return { ok: true, json: async () => ({}) }
    }
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
  await screen.findByRole('heading', { name: 'Bridge job' })

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

test('a dropped SSE stream marks the row failed instead of leaving it pending forever', async () => {
  mockApi()
  render(<App />)

  await screen.findByRole('heading', { name: 'Bridge job' })

  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  es.emit('entity_resolved', { entity: { name: 'Cives Steel', domain: 'cives.com' } })

  // stream drops before completion
  es.fail()

  // the verdict cell shows failed, not a perpetual pending spinner
  await waitFor(() => expect(document.querySelector('.vendor-row .failed')).not.toBeNull())
  expect(document.querySelector('.vendor-row .dot')).toBeNull()
  expect(screen.getByText(/stream dropped/i)).toBeInTheDocument()
})

test('selecting an already-generated vendor fetches its report from the REST endpoint', async () => {
  const projects = [
    { id: 1, name: 'Bridge job', created_at: 't' },
    { id: 2, name: 'Tunnel job', created_at: 't' },
  ]
  const projectDetails = {
    1: {
      id: 1, name: 'Bridge job', created_at: 't',
      vendors: [{
        vendor_id: 9, name: 'Cives Steel', vendor_key: 'cives-steel', generated: true,
        verdict_score: 7, verdict_reasoning: 'Solid.',
        dimensions: [{ dimension: 'legal', score: 8, as_of: '2026-06' }],
      }],
    },
    2: { id: 2, name: 'Tunnel job', created_at: 't', vendors: [] },
  }
  const vendorReports = {
    9: {
      generated: true, vendor_key: 'cives-steel',
      entity: { name: 'Cives Steel', domain: 'cives.com', country: null, industry: null, parent: null, is_public: false, ticker: null, exchange: null },
      verdict_score: 7, verdict_reasoning: 'Solid.',
      sections: [{ dimension: 'legal', score: 8, reasoning: 'clean',
                   findings: [{ claim: 'No litigation.', citation: { url: 'https://x.com', title: 'X', source_type: 'independent', score: 0.9, as_of: '2026-06' } }] }],
    },
  }
  const { fetchMock } = mockApi({ projects, projectDetails, vendorReports })
  render(<App />)

  await screen.findByRole('heading', { name: 'Bridge job' })
  const row = await screen.findByText('Cives Steel')
  await userEvent.click(row)

  await screen.findByText('No litigation.')
  expect(fetchMock.mock.calls.some(([u]) => String(u).match(/\/vendors\/9\/report$/))).toBe(true)
})

test('switching the active project closes the previous project\'s open streams', async () => {
  const projects = [
    { id: 1, name: 'Bridge job', created_at: 't' },
    { id: 2, name: 'Tunnel job', created_at: 't' },
  ]
  const projectDetails = {
    1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [] },
    2: { id: 2, name: 'Tunnel job', created_at: 't', vendors: [] },
  }
  mockApi({ projects, projectDetails })
  render(<App />)

  await screen.findByRole('heading', { name: 'Bridge job' })

  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  expect(es.closed).toBe(false)

  await userEvent.click(screen.getByRole('button', { name: /Tunnel job/ }))
  await waitFor(() => expect(es.closed).toBe(true))
})

test('a failed vendor deletion surfaces an error and keeps the row', async () => {
  mockApi({
    deleteVendor: () => ({ ok: false, status: 500, json: async () => ({}) }),
  })
  render(<App />)

  await screen.findByRole('heading', { name: 'Bridge job' })
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  await userEvent.click(screen.getByRole('button', { name: /delete vendor/i }))

  await waitFor(() => expect(document.querySelector('.error-banner')).not.toBeNull())
  expect(screen.getByText('Cives Steel')).toBeInTheDocument()
})

test('a vendor add that resolves after switching projects does not appear in the new project', async () => {
  const projects = [
    { id: 1, name: 'Bridge job', created_at: 't' },
    { id: 2, name: 'Tunnel job', created_at: 't' },
  ]
  const projectDetails = {
    1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [] },
    2: { id: 2, name: 'Tunnel job', created_at: 't', vendors: [] },
  }
  let resolvePost: (v?: unknown) => void
  const postPromise = new Promise<void>((res) => { resolvePost = () => res() })
  mockApi({
    projects,
    projectDetails,
    addVendor: async () => {
      await postPromise
      return { ok: true, json: async () => ({ id: 5, project_id: 1, name: 'Cives Steel', vendor_key: null, created_at: 't' }) }
    },
  })
  render(<App />)

  await screen.findByRole('heading', { name: 'Bridge job' })

  // add a vendor to project 1 (POST pending)
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))

  // switch to project 2 before the POST resolves
  await userEvent.click(screen.getByRole('button', { name: /Tunnel job/ }))
  await screen.findByRole('heading', { name: 'Tunnel job' })

  // resolve the pending POST
  resolvePost!({})

  // assert project 2's table does NOT show the vendor that was added to project 1
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Tunnel job' })).toBeInTheDocument())
  expect(screen.queryByText('Cives Steel')).not.toBeInTheDocument()
})
