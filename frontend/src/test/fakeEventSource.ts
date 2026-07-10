type Listener = (e: MessageEvent) => void

// Minimal controllable EventSource: tests grab the latest instance and push frames.
export class FakeEventSource {
  static instances: FakeEventSource[] = []
  static last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }
  static reset() { FakeEventSource.instances = [] }

  url: string
  closed = false
  onerror: (() => void) | null = null
  private listeners: Record<string, Listener[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: Listener) {
    ;(this.listeners[type] ||= []).push(fn)
  }
  close() { this.closed = true }

  // --- test controls ---
  emit(type: string, data: unknown) {
    for (const fn of this.listeners[type] || [])
      fn({ data: JSON.stringify(data) } as MessageEvent)
  }
  fail() { this.onerror?.() }
}
