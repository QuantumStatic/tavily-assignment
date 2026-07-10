import { beforeEach, expect, test, vi } from 'vitest'
import { openReportStream } from '../stream'
import { FakeEventSource } from './fakeEventSource'

beforeEach(() => FakeEventSource.reset())

test('dispatches typed events and closes on report_complete', () => {
  const events: string[] = []
  openReportStream(1, (ev) => events.push(ev.type), () => {})
  const es = FakeEventSource.last()
  expect(es.url).toMatch(/\/vendors\/1\/report\/stream$/)

  es.emit('entity_resolved', { entity: { name: 'Cives' } })
  es.emit('section_complete', { section: { dimension: 'legal', score: 8, findings: [], reasoning: '' }, cached: false })
  es.emit('report_complete', { report: { verdict_score: 7, sections: [] } })

  expect(events).toEqual(['entity_resolved', 'section_complete', 'report_complete'])
  expect(es.closed).toBe(true)   // auto-closed on terminal event
})

test('onerror closes without reopening (credit-safety)', () => {
  const onError = vi.fn()
  openReportStream(2, () => {}, onError)
  const es = FakeEventSource.last()
  es.fail()
  expect(onError).toHaveBeenCalledOnce()
  expect(es.closed).toBe(true)
})

test('the returned dispose closes the stream', () => {
  const dispose = openReportStream(3, () => {}, () => {})
  const es = FakeEventSource.last()
  dispose()
  expect(es.closed).toBe(true)
})

test('a malformed frame closes the stream and calls onError instead of throwing', () => {
  const onEvent = vi.fn()
  const onError = vi.fn()
  openReportStream(4, onEvent, onError)
  const es = FakeEventSource.last()
  // simulate a frame whose data isn't valid JSON, bypassing the fake's JSON.stringify helper
  es.emitRaw('section_complete', '{not valid json')
  expect(onEvent).not.toHaveBeenCalled()
  expect(onError).toHaveBeenCalledOnce()
  expect(es.closed).toBe(true)
})
