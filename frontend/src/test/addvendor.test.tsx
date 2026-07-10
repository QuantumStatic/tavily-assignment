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
