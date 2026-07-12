export interface VendorOption { vendor_key: string; name: string }
export interface TrendPoint { month: string; score: number }
export interface TrendSeries { vendor_key: string; name: string; points: TrendPoint[] }
export interface TrendResponse { dimension: string; series: TrendSeries[] }
