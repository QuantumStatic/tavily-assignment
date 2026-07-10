import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render, renderHook, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeToggle } from '../components/ThemeToggle'
import { useTheme } from '../theme'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})
afterEach(() => vi.restoreAllMocks())

test('ThemeToggle shows the moon (go dark) affordance in light mode', () => {
  render(<ThemeToggle theme="light" onToggle={() => {}} />)
  expect(screen.getByRole('button', { name: /switch to dark mode/i })).toBeInTheDocument()
})

test('ThemeToggle shows the sun (go light) affordance in dark mode', () => {
  render(<ThemeToggle theme="dark" onToggle={() => {}} />)
  expect(screen.getByRole('button', { name: /switch to light mode/i })).toBeInTheDocument()
})

test('clicking the toggle fires onToggle', async () => {
  const onToggle = vi.fn()
  render(<ThemeToggle theme="light" onToggle={onToggle} />)
  await userEvent.click(screen.getByRole('button', { name: /switch to dark mode/i }))
  expect(onToggle).toHaveBeenCalledOnce()
})

test('useTheme defaults to light and reflects it on the document root', () => {
  renderHook(() => useTheme())
  expect(document.documentElement.getAttribute('data-theme')).toBe('light')
})

test('useTheme toggles the document theme and persists to localStorage', () => {
  const { result } = renderHook(() => useTheme())
  expect(result.current[0]).toBe('light')

  act(() => result.current[1]())

  expect(result.current[0]).toBe('dark')
  expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  expect(localStorage.getItem('vendor-dd-theme')).toBe('dark')
})

test('useTheme restores a previously stored theme', () => {
  localStorage.setItem('vendor-dd-theme', 'dark')
  const { result } = renderHook(() => useTheme())
  expect(result.current[0]).toBe('dark')
  expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
})
