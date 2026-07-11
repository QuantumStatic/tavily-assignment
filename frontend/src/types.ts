export interface Project { id: number; name: string; created_at: string }

export interface DimensionScore { dimension: string; score: number; as_of: string }

export interface VendorSummary {
  vendor_id: number
  name: string
  vendor_key: string | null
  generated: boolean
  sections_present: number
  sections_expected: number
  verdict_score: number | null
  verdict_reasoning: string | null
  dimensions: DimensionScore[]
}

export interface ProjectDetail {
  id: number; name: string; created_at: string; vendors: VendorSummary[]
}

export interface VendorOut {
  id: number; project_id: number; name: string; vendor_key: string | null; created_at: string
  existed: boolean   // true when this name was already in the project — same row, no new research
}

export interface Citation {
  url: string; title: string; source_type: string; score: number; as_of: string | null
}
export interface Finding { claim: string; citation: Citation }
export interface Section { dimension: string; findings: Finding[]; reasoning: string; score: number }

export interface EntityCard {
  name: string; domain: string | null; country: string | null; industry: string | null
  parent: string | null; is_public: boolean; ticker: string | null; exchange: string | null
}

export interface VendorReport {
  generated: boolean
  vendor_key: string | null
  entity: EntityCard | null
  sections_present: number
  sections_expected: number
  verdict_score: number | null
  verdict_reasoning: string | null
  sections: Section[]
}

// The engine Report carried in a report_complete SSE frame.
export interface Report {
  vendor_input: string
  entity: EntityCard
  sections: Section[]
  verdict_score: number
  verdict_reasoning: string
}

// SSE frame payloads, discriminated by `type` (the SSE event name, merged in by stream.ts).
export type ReportStreamEvent =
  | { type: 'entity_resolved'; entity: EntityCard }
  | { type: 'section_complete'; section: Section; cached: boolean }
  | { type: 'section_error'; dimension: string; message: string }
  | { type: 'report_complete'; report: Report }
  | { type: 'report_error'; message: string }
