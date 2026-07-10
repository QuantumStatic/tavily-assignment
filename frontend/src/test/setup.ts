import '@testing-library/jest-dom'
import { FakeEventSource } from './fakeEventSource'

// jsdom has no EventSource; install the controllable fake globally.
;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource

// jsdom in this config exposes a non-functional `localStorage` ({}), so install a
// minimal in-memory Storage so theme-preference reads/writes work under test.
class MemoryStorage {
  private store: Record<string, string> = {}
  get length() { return Object.keys(this.store).length }
  clear() { this.store = {} }
  getItem(key: string) { return key in this.store ? this.store[key] : null }
  setItem(key: string, value: string) { this.store[key] = String(value) }
  removeItem(key: string) { delete this.store[key] }
  key(i: number) { return Object.keys(this.store)[i] ?? null }
}
Object.defineProperty(globalThis, 'localStorage', {
  value: new MemoryStorage(), writable: true, configurable: true,
})

// jsdom here doesn't implement matchMedia; default to "no dark preference".
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false },
  })) as unknown as typeof window.matchMedia
}
