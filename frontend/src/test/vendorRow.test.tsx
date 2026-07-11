import { expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VendorRow } from '../components/VendorRow'
import type { RowState } from '../rows'

function row(over: Partial<RowState> = {}): RowState {
  return {
    vendorId: 1, name: 'Acme', vendorKey: 'acme.com',
    cells: {}, verdict: 'idle', status: 'idle', chosen: false, ...over,
  }
}

function renderRow(r: RowState, onChosen = vi.fn()) {
  render(<table><tbody>
    <VendorRow row={r} onSelect={() => {}} onDelete={() => {}} onRename={() => {}}
               onResume={() => {}} onChosen={onChosen} />
  </tbody></table>)
  return onChosen
}

test('clicking the chosen toggle marks it chosen without selecting the row', async () => {
  const onSelect = vi.fn(); const onChosen = vi.fn()
  render(<table><tbody>
    <VendorRow row={row()} onSelect={onSelect} onDelete={() => {}} onRename={() => {}}
               onResume={() => {}} onChosen={onChosen} />
  </tbody></table>)
  await userEvent.click(screen.getByRole('button', { name: /mark .* chosen/i }))
  expect(onChosen).toHaveBeenCalledWith(1, true)
  expect(onSelect).not.toHaveBeenCalled()
})

test('a chosen vendor shows the un-choose control', () => {
  renderRow(row({ chosen: true }))
  expect(screen.getByRole('button', { name: /unmark .* chosen/i })).toBeInTheDocument()
})
