import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Markdown } from '../components/Markdown'

test('renders bold and inline code', () => {
  const { container } = render(<Markdown text="Revenue was **€5.23B** per the `2024` report." />)
  expect(container.querySelector('strong')).toHaveTextContent('€5.23B')
  expect(container.querySelector('code')).toHaveTextContent('2024')
})

test('renders a bullet list', () => {
  const { container } = render(<Markdown text={'Concerns:\n- delay on Project X\n- cost overrun'} />)
  const items = container.querySelectorAll('ul li')
  expect(items).toHaveLength(2)
  expect(items[0]).toHaveTextContent('delay on Project X')
  expect(screen.getByText('Concerns:')).toBeInTheDocument()   // preceding paragraph
})

test('renders a numbered list', () => {
  const { container } = render(<Markdown text={'1. first\n2. second'} />)
  expect(container.querySelectorAll('ol li')).toHaveLength(2)
})

test('does not inject raw html (text is escaped)', () => {
  const { container } = render(<Markdown text={'<img src=x onerror=alert(1)> plain'} />)
  expect(container.querySelector('img')).toBeNull()          // rendered as text, not an element
  expect(container).toHaveTextContent('<img src=x onerror=alert(1)> plain')
})

test('plain text with no markup still renders as a paragraph', () => {
  const { container } = render(<Markdown text="Just a sentence." />)
  expect(container.querySelector('p')).toHaveTextContent('Just a sentence.')
})
