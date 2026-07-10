import { expect, test } from 'vitest'
import { rowFromSummary, startStreaming, reduceEvent } from '../rows'
import type { VendorSummary } from '../types'

const summary = (over: Partial<VendorSummary> = {}): VendorSummary => ({
  vendor_id: 1, name: 'Cives', vendor_key: 'cives.com', generated: false,
  sections_present: 0, sections_expected: 7,
  verdict_score: null, verdict_reasoning: null, dimensions: [], ...over,
})

test('rowFromSummary: not generated -> idle cells, idle verdict', () => {
  const r = rowFromSummary(summary())
  expect(r.status).toBe('idle')
  expect(r.verdict).toBe('idle')
  expect(r.cells.legal).toBe('idle')
})

test('rowFromSummary: generated -> scored cells + verdict', () => {
  const r = rowFromSummary(summary({
    generated: true, verdict_score: 6, verdict_reasoning: 'ok',
    dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
  }))
  expect(r.status).toBe('done')
  expect(r.verdict).toEqual({ score: 6 })
  expect(r.cells.legal).toEqual({ score: 8 })
})

test('rowFromSummary: carries sections_present/sections_expected onto the row', () => {
  const r = rowFromSummary(summary({
    generated: true, sections_present: 2, sections_expected: 7,
    verdict_score: null, verdict_reasoning: null,
    dimensions: [{ dimension: 'legal', score: 8, as_of: 't' }],
  }))
  expect(r.sectionsPresent).toBe(2)
  expect(r.sectionsExpected).toBe(7)
})

test('rowFromSummary: carries sections_present/sections_expected even when not generated', () => {
  const r = rowFromSummary(summary({ sections_present: 0, sections_expected: 7 }))
  expect(r.sectionsPresent).toBe(0)
  expect(r.sectionsExpected).toBe(7)
})

test('streaming lifecycle: pending -> scored -> failed -> complete', () => {
  let r = startStreaming(rowFromSummary(summary()))
  expect(r.status).toBe('streaming')

  r = reduceEvent(r, { type: 'entity_resolved', entity: { name: 'Cives' } as never })
  expect(r.cells.legal).toBe('pending')

  r = reduceEvent(r, {
    type: 'section_complete',
    section: { dimension: 'legal', score: 8, findings: [], reasoning: '' }, cached: false,
  })
  expect(r.cells.legal).toEqual({ score: 8 })

  r = reduceEvent(r, { type: 'section_error', dimension: 'financial', message: 'boom' })
  expect(r.cells.financial).toBe('failed')

  r = reduceEvent(r, {
    type: 'report_complete',
    report: { vendor_input: 'Cives', entity: { name: 'Cives' } as never,
              sections: [{ dimension: 'legal', score: 8, findings: [], reasoning: '' }],
              verdict_score: 7, verdict_reasoning: 'r' },
  })
  expect(r.status).toBe('done')
  expect(r.verdict).toEqual({ score: 7 })
  expect(r.report?.verdict_score).toBe(7)
})

test('report_error marks the row failed', () => {
  const r = reduceEvent(startStreaming(rowFromSummary(summary())),
    { type: 'report_error', message: 'fatal' })
  expect(r.status).toBe('error')
  expect(r.verdict).toBe('failed')
  expect(r.errorMsg).toBe('fatal')
})

test('report_error only flips still-pending cells to failed, leaving scored/failed cells alone', () => {
  let r = startStreaming(rowFromSummary(summary()))
  r = reduceEvent(r, {
    type: 'section_complete',
    section: { dimension: 'legal', score: 8, findings: [], reasoning: '' }, cached: false,
  })
  r = reduceEvent(r, { type: 'section_error', dimension: 'financial', message: 'boom' })
  // legal is scored, financial already failed, every other dimension is still pending
  expect(r.cells.legal).toEqual({ score: 8 })
  expect(r.cells.financial).toBe('failed')
  expect(r.cells.safety).toBe('pending')

  r = reduceEvent(r, { type: 'report_error', message: 'stream dropped' })
  expect(r.cells.legal).toEqual({ score: 8 })      // untouched: real score preserved
  expect(r.cells.financial).toBe('failed')          // untouched: already failed
  expect(r.cells.safety).toBe('failed')             // flipped: was pending
})
