import type { ReportStreamEvent } from './types'
import { api } from './api'

const EVENT_TYPES = [
  'entity_resolved', 'section_complete', 'section_error', 'report_complete', 'report_error',
] as const

/**
 * Open an SSE stream for a vendor's report. Calls onEvent for each frame; auto-closes on
 * the terminal report_complete/report_error. On a connection error it closes and calls
 * onError instead of letting the native EventSource auto-reconnect (a reconnect would
 * restart the backend generator and re-spend Tavily/Nebius credits). Returns a dispose fn.
 */
export function openReportStream(
  vendorId: number,
  onEvent: (ev: ReportStreamEvent) => void,
  onError: () => void,
): () => void {
  const es = new EventSource(api.streamUrl(vendorId))
  for (const type of EVENT_TYPES) {
    es.addEventListener(type, (e: MessageEvent) => {
      const data = JSON.parse(e.data)
      onEvent({ type, ...data } as ReportStreamEvent)
      if (type === 'report_complete' || type === 'report_error') es.close()
    })
  }
  es.onerror = () => { es.close(); onError() }
  return () => es.close()
}
