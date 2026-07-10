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
