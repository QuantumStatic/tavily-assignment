import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary } from '../components/ErrorBoundary'

function Throw(): never {
  throw new Error('boom')
}

test('renders a fallback message instead of crashing when a child throws', () => {
  const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
  render(
    <ErrorBoundary>
      <Throw />
    </ErrorBoundary>,
  )
  expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
  spy.mockRestore()
})

test('renders children normally when nothing throws', () => {
  render(
    <ErrorBoundary>
      <div>all good</div>
    </ErrorBoundary>,
  )
  expect(screen.getByText('all good')).toBeInTheDocument()
})
