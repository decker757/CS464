import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import ModalShell from './ModalShell'

// A trigger and the dialog it opens, which is the only arrangement in which
// "focus moves in" and "focus comes back" mean anything. Two buttons inside,
// so there is a first and a last for the tab trap to wrap between.
function Harness({ isSubmitting = false }: { isSubmitting?: boolean }) {
  const [isOpen, setIsOpen] = useState(false)
  return (
    <>
      <button onClick={() => setIsOpen(true)}>Open</button>
      {isOpen && (
        <ModalShell
          ariaLabel="Confirm something"
          title="Confirm something?"
          description="This cannot be undone."
          isSubmitting={isSubmitting}
          onClose={() => setIsOpen(false)}
        >
          <button onClick={() => setIsOpen(false)}>Cancel</button>
          <button>Confirm</button>
        </ModalShell>
      )}
    </>
  )
}

async function openDialog(isSubmitting = false) {
  const actor = userEvent.setup()
  render(<Harness isSubmitting={isSubmitting} />)
  await actor.click(screen.getByRole('button', { name: 'Open' }))
  return actor
}

// aria-modal="true" tells assistive technology the background is inert. These
// are the four things that have to be true for that to be the truth (#358),
// and every caller of ModalShell inherits them.
describe('ModalShell', () => {
  it('moves focus into the dialog when it opens', async () => {
    await openDialog()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  })

  it('closes on Escape', async () => {
    const actor = await openDialog()

    await actor.keyboard('{Escape}')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('returns focus to the element that opened it', async () => {
    const actor = await openDialog()

    await actor.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.getByRole('button', { name: 'Open' })).toHaveFocus()
  })

  it('keeps Tab inside the dialog, in both directions', async () => {
    const actor = await openDialog()
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    const confirm = screen.getByRole('button', { name: 'Confirm' })

    await actor.tab()
    expect(confirm).toHaveFocus()

    await actor.tab()
    expect(cancel).toHaveFocus() // wrapped, rather than out to the trigger

    await actor.tab({ shift: true })
    expect(confirm).toHaveFocus()
  })

  it('closes on a click on the backdrop but not on one inside the panel', async () => {
    const actor = await openDialog()
    const panel = screen.getByRole('dialog')

    await actor.click(screen.getByText('Confirm something?'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await actor.click(panel.parentElement as HTMLElement)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('ignores Escape and the backdrop while the request is in flight', async () => {
    // Every caller disables its Cancel button while submitting; a dismissal
    // that outruns its own response would be the same thing by another route.
    const actor = await openDialog(true)
    const panel = screen.getByRole('dialog')

    await actor.keyboard('{Escape}')
    await actor.click(panel.parentElement as HTMLElement)

    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
