import type { Project, ProjectDetail, VendorOut, VendorReport } from './types'

const BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`request failed: ${r.status}`)
  return r.json() as Promise<T>
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
  deleteVendor: (id: number) =>
    fetch(`${BASE}/vendors/${id}`, { method: 'DELETE' }).then((r) => {
      if (!r.ok) throw new Error(`request failed: ${r.status}`)
    }),
  getReport: (id: number) => fetch(`${BASE}/vendors/${id}/report`).then(json<VendorReport>),
}
