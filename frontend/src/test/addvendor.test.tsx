import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AddVendorForm } from '../components/AddVendorForm'

test('submits a trimmed vendor name and clears the input', async () => {
  const onAdd = vi.fn()
  render(<AddVendorForm onAdd={onAdd} />)
  const input = screen.getByPlaceholderText('Vendor name…')
  await userEvent.type(input, '  Cives Steel  ')
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  expect(onAdd).toHaveBeenCalledWith('Cives Steel')
  expect(input).toHaveValue('')
})

test('does not submit an empty name', async () => {
  const onAdd = vi.fn()
  render(<AddVendorForm onAdd={onAdd} />)
  await userEvent.click(screen.getByRole('button', { name: /add vendor/i }))
  expect(onAdd).not.toHaveBeenCalled()
})

test('the add-vendor button is disabled until the input has non-whitespace text', async () => {
  render(<AddVendorForm onAdd={vi.fn()} />)
  const button = screen.getByRole('button', { name: /add vendor/i })
  const input = screen.getByPlaceholderText('Vendor name…')

  expect(button).toBeDisabled()

  await userEvent.type(input, '  ')
  expect(button).toBeDisabled()   // whitespace-only still counts as empty

  await userEvent.type(input, 'Cives Steel')
  expect(button).toBeEnabled()

  await userEvent.clear(input)
  expect(button).toBeDisabled()
})
