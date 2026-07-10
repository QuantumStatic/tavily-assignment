import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sidebar } from '../components/Sidebar'

const projects = [
  { id: 1, name: 'Bridge job', created_at: 't' },
  { id: 2, name: 'HVAC Q3', created_at: 't' },
]

test('lists projects and highlights the active one', () => {
  render(<Sidebar projects={projects} activeId={2} onSelect={() => {}} onCreate={() => {}} />)
  expect(screen.getByText('Bridge job')).toBeInTheDocument()
  expect(screen.getByText('HVAC Q3').closest('li')).toHaveClass('active')
})

test('clicking a project selects it', async () => {
  const onSelect = vi.fn()
  render(<Sidebar projects={projects} activeId={1} onSelect={onSelect} onCreate={() => {}} />)
  await userEvent.click(screen.getByText('HVAC Q3'))
  expect(onSelect).toHaveBeenCalledWith(2)
})

test('the new-project form submits a trimmed name and clears', async () => {
  const onCreate = vi.fn()
  render(<Sidebar projects={[]} activeId={null} onSelect={() => {}} onCreate={onCreate} />)
  const input = screen.getByPlaceholderText('New project…')
  await userEvent.type(input, '  Q1 RFP  {enter}')
  expect(onCreate).toHaveBeenCalledWith('Q1 RFP')
  expect(input).toHaveValue('')
})

test('shows an empty-state message when there are no projects', () => {
  render(<Sidebar projects={[]} activeId={null} onSelect={() => {}} onCreate={() => {}} />)
  expect(screen.getByText(/create a project/i)).toBeInTheDocument()
})
