import { expect, test } from 'vitest'
import { filterByName } from '../filter'

const items = [{ name: 'Bridge job' }, { name: 'HVAC Q3' }, { name: 'Turbines RFP' }]

test('empty query returns everything', () => {
  expect(filterByName(items, '')).toHaveLength(3)
  expect(filterByName(items, '   ')).toHaveLength(3)
})

test('matches case- and space-insensitively on substring', () => {
  expect(filterByName(items, 'hvac').map((i) => i.name)).toEqual(['HVAC Q3'])
  expect(filterByName(items, '  BRIDGE ').map((i) => i.name)).toEqual(['Bridge job'])
})

test('no match returns empty', () => {
  expect(filterByName(items, 'zzz')).toEqual([])
})
