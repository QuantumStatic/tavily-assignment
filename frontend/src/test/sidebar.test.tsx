import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sidebar } from '../components/Sidebar'

const projects = [
  { id: 1, name: 'Bridge job', created_at: 't' },
  { id: 2, name: 'HVAC Q3', created_at: 't' },
]

function renderSidebar(props: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  return render(
    <Sidebar
      projects={props.projects ?? projects}
      activeId={props.activeId ?? null}
      onSelect={props.onSelect ?? (() => {})}
      onCreate={props.onCreate ?? (() => {})}
      onDelete={props.onDelete ?? (() => {})}
      onOverview={props.onOverview ?? (() => {})}
    />,
  )
}

test('lists projects and highlights the active one', () => {
  renderSidebar({ activeId: 2 })
  expect(screen.getByText('Bridge job')).toBeInTheDocument()
  expect(screen.getByText('HVAC Q3').closest('li')).toHaveClass('active')
})

test('clicking a project selects it', async () => {
  const onSelect = vi.fn()
  renderSidebar({ activeId: 1, onSelect })
  await userEvent.click(screen.getByText('HVAC Q3'))
  expect(onSelect).toHaveBeenCalledWith(2)
})

test('typing in the search filters the project list', async () => {
  renderSidebar()
  await userEvent.type(screen.getByRole('searchbox', { name: /search/i }), 'hvac')
  expect(screen.getByText('HVAC Q3')).toBeInTheDocument()
  expect(screen.queryByText('Bridge job')).toBeNull()
})

test('a query with no match offers a create option that calls onCreate', async () => {
  const onCreate = vi.fn()
  renderSidebar({ onCreate })
  await userEvent.type(screen.getByRole('searchbox', { name: /search/i }), 'Acme')
  // neither existing project is shown, and a create affordance appears
  expect(screen.queryByText('Bridge job')).toBeNull()
  const createBtn = screen.getByRole('button', { name: /create.*acme/i })
  await userEvent.click(createBtn)
  expect(onCreate).toHaveBeenCalledWith('Acme')
})

test('a query that exactly matches an existing project does not offer create', async () => {
  renderSidebar()
  await userEvent.type(screen.getByRole('searchbox', { name: /search/i }), 'bridge job')
  expect(screen.getByText('Bridge job')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /create/i })).toBeNull()
})

test('pressing Enter with no match creates the project and clears the search', async () => {
  const onCreate = vi.fn()
  renderSidebar({ projects: [], onCreate })
  const input = screen.getByRole('searchbox', { name: /search/i })
  await userEvent.type(input, '  Q1 RFP  {enter}')
  expect(onCreate).toHaveBeenCalledWith('Q1 RFP')
  expect(input).toHaveValue('')
})

test('pressing Enter with a single filtered match selects it', async () => {
  const onSelect = vi.fn()
  renderSidebar({ onSelect })
  await userEvent.type(screen.getByRole('searchbox', { name: /search/i }), 'hvac{enter}')
  expect(onSelect).toHaveBeenCalledWith(2)
})

test('shows an empty-state message when there are no projects and no query', () => {
  renderSidebar({ projects: [] })
  expect(screen.getByText(/create a project/i)).toBeInTheDocument()
})

test('clicking the delete button calls onDelete without triggering onSelect', async () => {
  const onSelect = vi.fn()
  const onDelete = vi.fn()
  renderSidebar({ activeId: 1, onSelect, onDelete })
  await userEvent.click(screen.getByRole('button', { name: /delete project hvac q3/i }))
  expect(onDelete).toHaveBeenCalledWith(2)
  expect(onSelect).not.toHaveBeenCalled()
})

test('shows an Overview entry that is active when no project is selected', async () => {
  const onOverview = vi.fn()
  render(
    <Sidebar projects={projects} activeId={null} onSelect={() => {}} onCreate={() => {}}
             onDelete={() => {}} onOverview={onOverview} />,
  )
  const overview = screen.getByRole('button', { name: /overview/i })
  expect(overview).toHaveClass('active')
  await userEvent.click(overview)
  expect(onOverview).toHaveBeenCalled()
})
