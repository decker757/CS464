import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import AccountMenu from './AccountMenu'

// The menu beside something else that can be clicked, which is the only
// arrangement in which "a click outside closes it" means anything.
function renderMenu() {
  render(
    <>
      <AccountMenu username="alice" email="alice@smu.edu.sg" isLoggingOut={false} onLogout={() => {}} />
      <button>Somewhere else</button>
    </>,
  )
  return userEvent.setup()
}

function accountButton() {
  return screen.getByRole('button', { name: 'alice' })
}

// The account menu holds sign-out, so these are the four things [#437] asks of
// it: it opens on a click, it moves focus, and both Escape and a click
// outside close it. They are `useFocusTrap`'s promises, which ModalShell
// makes as well, asserted here for the one caller that is not a dialog.
describe('AccountMenu', () => {
  it('opens on a click and moves focus into the menu', async () => {
    const actor = renderMenu()
    expect(screen.queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()

    await actor.click(accountButton())

    expect(screen.getByRole('button', { name: /log out/i })).toHaveFocus()
  })

  it('closes on Escape, and gives focus back to the account button', async () => {
    const actor = renderMenu()
    await actor.click(accountButton())

    await actor.keyboard('{Escape}')

    expect(screen.queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()
    expect(accountButton()).toHaveFocus()
  })

  it('closes on a click outside it', async () => {
    const actor = renderMenu()
    await actor.click(accountButton())

    await actor.click(screen.getByRole('button', { name: 'Somewhere else' }))

    expect(screen.queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()
  })

  it('closes again on a second click of the account button', async () => {
    // The click that closes it starts outside the panel but inside the menu,
    // so a handler that read it as a click elsewhere would close the menu and
    // let the button's own toggle reopen it.
    const actor = renderMenu()
    await actor.click(accountButton())

    await actor.click(accountButton())

    expect(screen.queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()
  })
})
