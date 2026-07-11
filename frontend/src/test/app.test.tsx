import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import { FakeEventSource } from './fakeEventSource'
import { DIMENSIONS } from '../dimensions'

type MockApiOptions = {
  projects?: { id: number; name: string; created_at: string }[]
  projectDetails?: Record<number, { id: number; name: string; created_at: string; vendors: unknown[] }>
  vendorReports?: Record<number, unknown>
  deleteVendor?: (id: number) => { ok: boolean; status?: number; json: () => Promise<unknown> }
  addVendor?: () => Promise<{ ok: boolean; json: () => Promise<unknown> }>
  statsOverride?: Record<string, unknown>
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
    const renameVendorMatch = u.match(/\/vendors\/(\d+)$/)
    if (renameVendorMatch && method === 'PATCH') {
      const id = Number(renameVendorMatch[1])
      const body = init?.body ? JSON.parse(String(init.body)) : {}
      return { ok: true, json: async () => ({ id, project_id: 1, name: body.name, vendor_key: null, created_at: 't', existed: false }) }
    }
    const reportMatch = u.match(/\/vendors\/(\d+)\/report$/)
    if (reportMatch && method === 'GET') {
      const id = Number(reportMatch[1])
      const report = vendorReports[id]
      if (report) return { ok: true, json: async () => report }
    }
    if (u.match(/\/stats(\?|$)/) && method === 'GET') {
      return {
        ok: true, json: async () => ({
          projects_total: projects.length, projects_selected: projects.length,
          vendors_total: 0, vendors_generated: 0,
          avg_verdict: null, risk_high: 0, risk_watch: 0, risk_cleared: 0,
          independent_sources: 0, self_reported_sources: 0,
          verdict_histogram: Array(11).fill(0), dimension_avgs: {}, dimension_histograms: {},
          weakest_dimension: null, weakest_low_count: 0,
          shortlist: [], red_flags: [], most_trusted: [],
          ...opts.statsOverride,
        }),
      }
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

// App now lands on the Overview (activeId === null) and no longer auto-selects the
// first project, so tests must explicitly navigate into a project via the sidebar.
async function openProject(name: string) {
  await userEvent.click(await screen.findByText(name))
  await screen.findByRole('heading', { name })
}

test('the theme toggle is present regardless of whether a project is active', async () => {
  mockApi({ projects: [] })
  render(<App />)

  // no projects yet -> lands on the Overview, but the toggle still renders in the toolbar
  await screen.findByRole('heading', { name: 'Overview' })
  expect(screen.getByText(/select at least one project/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /switch to (dark|light) mode/i })).toBeInTheDocument()
})

test('add a vendor, watch cells stream in, open the report panel', async () => {
  mockApi()
  render(<App />)

  // project loads into the sidebar and auto-selects
  await openProject('Bridge job')

  // the theme toggle is still present when a project is active
  expect(screen.getByRole('button', { name: /switch to (dark|light) mode/i })).toBeInTheDocument()

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

const completedReport = {
  generated: true, vendor_key: 'cives.com',
  entity: { name: 'Cives Steel', domain: 'cives.com', country: null, industry: null,
            parent: null, is_public: false, ticker: null, exchange: null },
  verdict_score: 7, verdict_reasoning: 'Solid.',
  // fully generated: one section per dimension, so no cell is left "failed"
  sections: DIMENSIONS.map((d) => ({ dimension: d.key, score: 8, findings: [], reasoning: 'clean' })),
  sections_present: DIMENSIONS.length, sections_expected: DIMENSIONS.length,
}

test('a dropped SSE stream falls back to polling the report, not failing the row', async () => {
  // The backend finishes and caches the report even when the stream dies —
  // the row must recover via GET /report instead of telling the user to
  // delete and re-add (which would evict the completing report).
  mockApi({ vendorReports: { 5: completedReport } })
  render(<App />)

  await openProject('Bridge job')
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  es.emit('entity_resolved', { entity: { name: 'Cives Steel', domain: 'cives.com' } })
  es.fail()   // stream drops; the immediate poll finds the finished report

  await waitFor(() => expect(screen.getByText('7/10')).toBeInTheDocument())
  expect(screen.queryByText(/stream dropped/i)).toBeNull()
  expect(document.querySelector('.vendor-row .failed')).toBeNull()
})

test('while the polled report is still generating, the row keeps streaming', async () => {
  mockApi({ vendorReports: { 5: { ...completedReport, generated: false, sections: [],
                                   verdict_score: null, verdict_reasoning: null, entity: null } } })
  render(<App />)

  await openProject('Bridge job')
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  es.emit('entity_resolved', { entity: { name: 'Cives Steel', domain: 'cives.com' } })
  es.fail()

  // the immediate poll returns generated:false -> still streaming, no failure UI
  await waitFor(() =>
    expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).endsWith('/vendors/5/report'))).toBe(true))
  expect(document.querySelector('.vendor-row .dot')).not.toBeNull()
  expect(screen.queryByText(/stream dropped/i)).toBeNull()
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

  await openProject('Bridge job')
  const row = await screen.findByText('Cives Steel')
  await userEvent.click(row)

  await screen.findByText('No litigation.')
  expect(fetchMock.mock.calls.some(([u]) => String(u).match(/\/vendors\/9\/report$/))).toBe(true)
})

test('clicking a dashboard shortlist row navigates into the project and actually loads the report', async () => {
  // Regression test: openVendorFromDashboard used to only set selectedVendorId
  // without fetching the report, leaving the panel stuck on "still generating"
  // even though the vendor was fully researched.
  const projects = [{ id: 1, name: 'Bridge job', created_at: 't' }]
  const projectDetails = {
    1: {
      id: 1, name: 'Bridge job', created_at: 't',
      vendors: [{
        vendor_id: 9, name: 'Fluor Corporation', vendor_key: 'fluor.com', generated: true,
        sections_present: 6, sections_expected: 6,
        verdict_score: 5, verdict_reasoning: 'Weakest area: legal.',
        dimensions: [{ dimension: 'legal', score: 2, as_of: '2026-06' }],
      }],
    },
  }
  const vendorReports = {
    9: {
      generated: true, vendor_key: 'fluor.com',
      entity: { name: 'Fluor Corporation', domain: 'fluor.com', country: null, industry: null, parent: null, is_public: false, ticker: null, exchange: null },
      verdict_score: 5, verdict_reasoning: 'Weakest area: legal.',
      sections: [{ dimension: 'legal', score: 2, reasoning: 'Elevated legal risk.',
                   findings: [{ claim: 'Adverse judgment reported.', citation: { url: 'https://x.com', title: 'X', source_type: 'independent', score: 0.9, as_of: '2026-06' } }] }],
      sections_present: 6, sections_expected: 6, chosen_count: 1, projects_count: 1, dimension_deltas: {},
    },
  }
  const statsOverride = {
    vendors_total: 1, vendors_generated: 1, avg_verdict: 5,
    shortlist: [{
      ref: { vendor_id: 9, name: 'Fluor Corporation', project_id: 1, project_name: 'Bridge job' },
      verdict: 5, previous_verdict: null,
    }],
  }
  mockApi({ projects, projectDetails, vendorReports, statsOverride })
  render(<App />)

  // lands on the Overview by default; the shortlist row is a drill-through target
  await screen.findByRole('heading', { name: 'Overview' })
  const shortlistRow = await screen.findByText('Fluor Corporation')
  await userEvent.click(shortlistRow)

  // navigated into the project AND the report actually loaded (not stuck on
  // "still generating") — the finding text proves fetchAndSetReport ran.
  await screen.findByRole('heading', { name: 'Bridge job' })
  await screen.findByText('Adverse judgment reported.')
  expect(screen.queryByText(/still generating/i)).toBeNull()
})

test('selecting a partially-generated vendor shows a still-generating note instead of a blank panel', async () => {
  const projects = [{ id: 1, name: 'Bridge job', created_at: 't' }]
  const projectDetails = {
    1: {
      id: 1, name: 'Bridge job', created_at: 't',
      vendors: [{
        vendor_id: 9, name: 'Cives Steel', vendor_key: 'cives-steel', generated: true,
        sections_present: 2, sections_expected: 6,
        verdict_score: null, verdict_reasoning: null,
        dimensions: [{ dimension: 'legal', score: 8, as_of: '2026-06' }],
      }],
    },
  }
  // the read-model report is only partially assembled: sections exist but the verdict
  // hasn't been computed yet, so entity/verdict_score/verdict_reasoning are still null
  const vendorReports = {
    9: {
      generated: true, vendor_key: 'cives-steel', entity: null,
      verdict_score: null, verdict_reasoning: null,
      sections: [{ dimension: 'legal', score: 8, reasoning: 'clean', findings: [] }],
      sections_present: 2, sections_expected: 6,
    },
  }
  mockApi({ projects, projectDetails, vendorReports })
  render(<App />)

  await openProject('Bridge job')
  const row = await screen.findByText('Cives Steel')
  await userEvent.click(row)

  await screen.findByText(/2 of 6/i)
  expect(screen.queryByText(/generating report…/i)).not.toBeInTheDocument()
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

  await openProject('Bridge job')

  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  expect(es.closed).toBe(false)

  await userEvent.click(screen.getByText('Tunnel job'))
  await waitFor(() => expect(es.closed).toBe(true))
})

test('adding an already-present vendor reuses the row and opens its report, no new stream', async () => {
  const existingRow = {
    vendor_id: 9, name: 'Cives Steel', vendor_key: 'cives-steel', generated: true,
    sections_present: 6, sections_expected: 6,
    verdict_score: 7, verdict_reasoning: 'Solid.',
    dimensions: [{ dimension: 'legal', score: 8, as_of: '2026-06' }],
  }
  mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [existingRow] } },
    vendorReports: {
      9: { generated: true, vendor_key: 'cives-steel', entity: { name: 'Cives Steel' },
          verdict_score: 7, verdict_reasoning: 'Solid.',
          sections: [{ dimension: 'legal', score: 8, reasoning: 'clean', findings: [] }],
          sections_present: 6, sections_expected: 6 },
    },
    addVendor: async () => ({
      ok: true, json: async () => ({
        id: 9, project_id: 1, name: 'Cives Steel', vendor_key: 'cives-steel',
        created_at: 't', existed: true,
      }),
    }),
  })
  render(<App />)

  await openProject('Bridge job')
  await screen.findByText('Cives Steel')   // the row is already there before we "add" it

  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))

  // its existing report opens — no error, no new row, no new EventSource
  await screen.findByText('Solid.')
  expect(screen.getAllByText('Cives Steel').length).toBeGreaterThan(0)
  expect(document.querySelector('.error-banner')).toBeNull()
  expect(FakeEventSource.last()).toBeUndefined()
})

test('a successful vendor deletion closes its stream and removes the row', async () => {
  mockApi()
  render(<App />)

  await openProject('Bridge job')
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  expect(es.closed).toBe(false)

  await userEvent.click(screen.getByRole('button', { name: /delete vendor/i }))

  await waitFor(() => expect(screen.queryByText('Cives Steel')).not.toBeInTheDocument())
  expect(es.closed).toBe(true)
})

test('a failed vendor deletion surfaces an error and keeps the row', async () => {
  mockApi({
    deleteVendor: () => ({ ok: false, status: 500, json: async () => ({}) }),
  })
  render(<App />)

  await openProject('Bridge job')
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  expect(es.closed).toBe(false)

  await userEvent.click(screen.getByRole('button', { name: /delete vendor/i }))

  await waitFor(() => expect(document.querySelector('.error-banner')).not.toBeNull())
  expect(screen.getByText('Cives Steel')).toBeInTheDocument()
  expect(es.closed).toBe(false)

  // the banner is announced to assistive tech and has a visible, accessible dismiss control
  expect(screen.getByRole('alert')).toBe(document.querySelector('.error-banner'))
  await userEvent.click(screen.getByRole('button', { name: /dismiss error/i }))
  expect(document.querySelector('.error-banner')).toBeNull()
})

test('deleting a vendor that is already gone (404) still removes the row, no error', async () => {
  mockApi({
    deleteVendor: () => ({ ok: false, status: 404, json: async () => ({ detail: 'vendor not found' }) }),
  })
  render(<App />)

  await openProject('Bridge job')
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  await screen.findByText('Cives Steel')

  await userEvent.click(screen.getByRole('button', { name: /delete vendor/i }))

  await waitFor(() => expect(screen.queryByText('Cives Steel')).not.toBeInTheDocument())
  expect(document.querySelector('.error-banner')).toBeNull()
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

  await openProject('Bridge job')

  // add a vendor to project 1 (POST pending)
  await userEvent.type(screen.getByPlaceholderText('Vendor name…'), 'Cives Steel')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))

  // switch to project 2 before the POST resolves
  await userEvent.click(screen.getByText('Tunnel job'))
  await screen.findByRole('heading', { name: 'Tunnel job' })

  // resolve the pending POST
  resolvePost!({})

  // assert project 2's table does NOT show the vendor that was added to project 1
  await waitFor(() => expect(screen.getByRole('heading', { name: 'Tunnel job' })).toBeInTheDocument())
  expect(screen.queryByText('Cives Steel')).not.toBeInTheDocument()
})

test('deleting the active project removes it and falls back to another project', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const { fetchMock } = mockApi({
    projects: [
      { id: 1, name: 'Bridge job', created_at: 't' },
      { id: 2, name: 'Tunnel job', created_at: 't' },
    ],
    projectDetails: {
      1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [] },
      2: { id: 2, name: 'Tunnel job', created_at: 't', vendors: [] },
    },
  })
  render(<App />)
  await openProject('Bridge job')

  await userEvent.click(screen.getByRole('button', { name: /delete project bridge job/i }))

  await waitFor(() => expect(
    fetchMock.mock.calls.some(([u, init]) =>
      String(u).endsWith('/projects/1') && init?.method === 'DELETE')).toBe(true))
  await screen.findByRole('heading', { name: 'Tunnel job' })
  expect(screen.queryByText('Bridge job')).toBeNull()
})

test('renaming a vendor PATCHes the API and updates the row in place', async () => {
  const doneVendor = {
    vendor_id: 9, name: 'Cives Stel', vendor_key: 'cives.com', generated: true,
    sections_present: 6, sections_expected: 6, verdict_score: 7, verdict_reasoning: 'ok',
    dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
  }
  const { fetchMock } = mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [doneVendor] } },
  })
  render(<App />)
  await openProject('Bridge job')
  await screen.findByText('Cives Stel')

  await userEvent.click(screen.getByRole('button', { name: /rename vendor/i }))
  const input = screen.getByRole('textbox', { name: /new vendor name/i })
  await userEvent.clear(input)
  await userEvent.type(input, 'Cives Steel{Enter}')

  await waitFor(() => expect(
    fetchMock.mock.calls.some(([u, init]) =>
      String(u).endsWith('/vendors/9') && init?.method === 'PATCH')).toBe(true))
  await screen.findByText('Cives Steel')
  expect(screen.queryByText('Cives Stel')).toBeNull()
})

const partialVendor = {
  vendor_id: 9, name: 'Voith', vendor_key: 'voith.com', generated: true,
  sections_present: 2, sections_expected: 6, verdict_score: 5, verdict_reasoning: 'thin',
  dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
}

test('a partial report row offers resume, and clicking it re-opens the stream', async () => {
  mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [partialVendor] } },
  })
  render(<App />)
  await openProject('Bridge job')
  await screen.findByText('Voith')

  await userEvent.click(screen.getByRole('button', { name: /resume research/i }))

  const es = await waitFor(() => {
    const e = FakeEventSource.last()
    if (!e) throw new Error('no stream yet')
    return e
  })
  expect(es.url).toContain('/vendors/9/report/stream')
  expect(document.querySelector('.vendor-row .dot')).not.toBeNull()
})

test('a fully generated row does not offer resume', async () => {
  mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't',
      vendors: [{ ...partialVendor, sections_present: 6 }] } },
  })
  render(<App />)
  await openProject('Bridge job')
  await screen.findByText('Voith')
  expect(screen.queryByRole('button', { name: /resume research/i })).toBeNull()
})

test('a domain-level duplicate row shows a badge naming the canonical vendor', async () => {
  mockApi({
    projectDetails: { 1: { id: 1, name: 'Bridge job', created_at: 't', vendors: [
      { ...partialVendor, sections_present: 6, duplicate_of: null },
      { ...partialVendor, vendor_id: 10, name: 'Voith Hydro', sections_present: 6, duplicate_of: 'Voith' },
    ] } },
  })
  render(<App />)
  await openProject('Bridge job')
  await screen.findByText('Voith Hydro')
  const badge = screen.getByTitle(/same company as Voith/i)
  expect(badge).toBeInTheDocument()
})
