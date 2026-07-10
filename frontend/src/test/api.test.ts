import { afterEach, expect, test, vi } from 'vitest'
import { api } from '../api'

afterEach(() => vi.restoreAllMocks())

test('createProject POSTs name and returns the project', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ id: 1, name: 'P', created_at: 't' }),
  })
  vi.stubGlobal('fetch', fetchMock)
  const p = await api.createProject('P')
  expect(p.id).toBe(1)
  const [url, init] = fetchMock.mock.calls[0]
  expect(String(url)).toMatch(/\/projects$/)
  expect(init.method).toBe('POST')
  expect(JSON.parse(init.body)).toEqual({ name: 'P' })
})

test('getProject GETs the detail read-model', async () => {
  const detail = { id: 1, name: 'P', created_at: 't', vendors: [] }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => detail }))
  expect(await api.getProject(1)).toEqual(detail)
})

test('a non-ok response rejects', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }))
  await expect(api.getProject(9)).rejects.toThrow()
})

test('a non-ok response with a detail body surfaces that detail as the error message', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false, status: 404, json: async () => ({ detail: 'vendor not found' }),
  }))
  await expect(api.getReport(9)).rejects.toThrow('vendor not found')
})

test('a non-ok response with no parseable body falls back to a generic message', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false, status: 500, json: async () => { throw new Error('not json') },
  }))
  await expect(api.getReport(9)).rejects.toThrow('request failed: 500')
})

test('deleteVendor rejects with the detail body on a non-ok response', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false, status: 404, json: async () => ({ detail: 'vendor not found' }),
  }))
  await expect(api.deleteVendor(9)).rejects.toThrow('vendor not found')
})
