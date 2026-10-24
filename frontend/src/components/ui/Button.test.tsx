import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import Button from './Button'

describe('Button', () => {
  // #452 restyled the disabled state so that a button nobody can press stops
  // looking pressable. The fix is a look, and a look is the easy half to undo:
  // the other half of the promise is that `disabled` is the real attribute, so
  // the control is inert and out of the tab order whatever it looks like.
  // That is also what `ModalShell`'s focus trap selects on
  // (`button:not([disabled])`), and what the ticket's keyboard paragraph
  // invites somebody to swap for `aria-disabled` — which would leave the
  // button clickable and every caller's guard with it.
  it('does not run its handler while it is disabled', async () => {
    const onClick = vi.fn()
    render(<Button variant="primary" size="block" disabled onClick={onClick}>Buy Yes</Button>)

    const button = screen.getByRole('button', { name: 'Buy Yes' })
    expect(button).toBeDisabled()
    await userEvent.click(button)

    expect(onClick).not.toHaveBeenCalled()
  })
})
