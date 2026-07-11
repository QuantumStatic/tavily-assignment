import type { Project, ProjectDetail, VendorOut, VendorReport } from './types'
import type { DashboardStats } from './dashboard'

const BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

async function ensureOk(r: Response): Promise<Response> {
  if (r.ok) return r
  let detail: string | undefined
  try {
    const body = await r.json()
    detail = typeof body?.detail === 'string' ? body.detail : undefined
  } catch {
    // body wasn't JSON (or empty) — fall through to the generic message
  }
  throw new Error(detail ?? `request failed: ${r.status}`)
}

async function json<T>(r: Response): Promise<T> {
  return (await ensureOk(r)).json() as Promise<T>
}

const JSON_HEADERS = { 'content-type': 'application/json' }

export const api = {
  base: BASE,
  streamUrl: (vendorId: number) => `${BASE}/vendors/${vendorId}/report/stream`,

  listProjects: () => fetch(`${BASE}/projects`).then(json<Project[]>),
  createProject: (name: string) =>
    fetch(`${BASE}/projects`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ name }) })
      .then(json<Project>),
  getProject: (id: number) => fetch(`${BASE}/projects/${id}`).then(json<ProjectDetail>),
  addVendor: (projectId: number, name: string) =>
    fetch(`${BASE}/projects/${projectId}/vendors`, {
      method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ name }),
    }).then(json<VendorOut>),
  renameVendor: (id: number, name: string) =>
    fetch(`${BASE}/vendors/${id}`, {
      method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ name }),
    }).then(json<VendorOut>),
  deleteVendor: (id: number) =>
    fetch(`${BASE}/vendors/${id}`, { method: 'DELETE' }).then((r) => {
      // 404 means it's already gone server-side — that's the outcome we wanted, so
      // succeed and let the caller drop the row rather than stranding it in the UI.
      if (r.status === 404) return undefined
      return ensureOk(r).then(() => undefined)
    }),
  getReport: (id: number) => fetch(`${BASE}/vendors/${id}/report`).then(json<VendorReport>),
  deleteProject: (id: number) =>
    fetch(`${BASE}/projects/${id}`, { method: 'DELETE' }).then((r) => {
      if (r.status === 404) return undefined   // already gone -> desired outcome
      return ensureOk(r).then(() => undefined)
    }),
  getStats: (projectIds?: number[]) => {
    const q = projectIds && projectIds.length ? `?projects=${projectIds.join(',')}` : ''
    return fetch(`${BASE}/stats${q}`).then(json<DashboardStats>)
  },
  setChosen: (id: number, chosen: boolean) =>
    fetch(`${BASE}/vendors/${id}/chosen`, {
      method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ chosen }),
    }).then(json<VendorOut>),
}
