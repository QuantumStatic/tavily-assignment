import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProjectFilter } from '../components/ProjectFilter'

const projects = [
  { id: 1, name: 'Bridge job', vendorCount: 5 },
  { id: 2, name: 'HVAC Q3', vendorCount: 6 },
  { id: 3, name: 'Turbines RFP', vendorCount: 3 },
]

function setup(selected = new Set([1, 2, 3]), onChange = vi.fn()) {
  render(<ProjectFilter projects={projects} selectedIds={selected} onChange={onChange} />)
  return onChange
}

async function open() {
  await userEvent.click(screen.getByRole('button', { name: /projects/i }))
}

test('trigger summarizes all selected', () => {
  setup()
  expect(screen.getByRole('button', { name: /all \(3\)/i })).toBeInTheDocument()
})

test('trigger summarizes a subset', () => {
  setup(new Set([1]))
  expect(screen.getByRole('button', { name: /1 of 3/i })).toBeInTheDocument()
})

test('search narrows the option rows', async () => {
  setup()
  await open()
  await userEvent.type(screen.getByRole('searchbox', { name: /filter projects/i }), 'hvac')
  expect(screen.getByText('HVAC Q3')).toBeInTheDocument()
  expect(screen.queryByText('Bridge job')).toBeNull()
})

test('toggling an option emits the new selection', async () => {
  const onChange = setup(new Set([1, 2, 3]))
  await open()
  await userEvent.click(screen.getByText('Bridge job'))
  expect(onChange).toHaveBeenCalledWith(new Set([2, 3]))
})

test('deselect all emits empty set', async () => {
  const onChange = setup(new Set([1, 2, 3]))
  await open()
  await userEvent.click(screen.getByRole('button', { name: /^deselect all$/i }))
  expect(onChange).toHaveBeenCalledWith(new Set())
})

test('select all emits every id', async () => {
  const onChange = setup(new Set([1]))
  await open()
  await userEvent.click(screen.getByRole('button', { name: /^select all$/i }))
  expect(onChange).toHaveBeenCalledWith(new Set([1, 2, 3]))
})

test('shows vendor counts on rows', async () => {
  setup()
  await open()
  expect(screen.getByText(/5 vendors/i)).toBeInTheDocument()
})

test('clicking outside the dropdown closes it', async () => {
  setup()
  await open()
  expect(screen.getByText('HVAC Q3')).toBeInTheDocument()
  await userEvent.click(document.body)
  expect(screen.queryByText('HVAC Q3')).toBeNull()
})

test('clicking inside the dropdown does not close it', async () => {
  setup()
  await open()
  await userEvent.click(screen.getByRole('searchbox', { name: /filter projects/i }))
  expect(screen.getByText('HVAC Q3')).toBeInTheDocument()
})
