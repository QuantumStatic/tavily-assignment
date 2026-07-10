import '@testing-library/jest-dom'
import { FakeEventSource } from './fakeEventSource'

// jsdom has no EventSource; install the controllable fake globally.
;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource
