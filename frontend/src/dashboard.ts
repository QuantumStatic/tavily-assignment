export interface VendorRef { vendor_id: number; name: string; project_id: number; project_name: string }
export interface ShortlistEntry { ref: VendorRef; verdict: number; previous_verdict: number | null }
export interface RedFlag { ref: VendorRef; kind: 'dimension' | 'delivery_risk'; dimension: string | null; score: number; detail: string }
export interface TrustedVendor { name: string; vendor_key: string; chosen_count: number; project_count: number; verdict: number | null }

export interface DashboardStats {
  projects_total: number; projects_selected: number
  vendors_total: number; vendors_generated: number
  avg_verdict: number | null
  risk_high: number; risk_watch: number; risk_cleared: number
  independent_sources: number; self_reported_sources: number
  verdict_histogram: number[]
  dimension_avgs: Record<string, number>
  dimension_histograms: Record<string, number[]>
  weakest_dimension: string | null
  weakest_low_count: number
  shortlist: ShortlistEntry[]
  red_flags: RedFlag[]
  most_trusted: TrustedVendor[]
}
