import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import TopBar from './TopBar'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

// `undefined` is its own case, not a repeat of the guest one: the session
// starts unknown and stays that way for a whole /auth/me plus /auth/refresh,
// which is most of a first-time visitor's landing page. A rule written as
// "null means the landing page" would send that visitor's wordmark to a
// protected route and leave the other two cases green.
const cases: Array<[string, User | null | undefined, string]> = [
  ['a signed-in user', trader, '/markets'],
  ['a guest', null, '/'],
  ['a visitor whose session is not known yet', undefined, '/'],
]

function renderTopBar(user: User | null | undefined) {
  render(
    <WithProviders user={user}>
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('TopBar', () => {
  it.each(cases)('links the logo home for %s', (_who, user, destination) => {
    renderTopBar(user)

    const logoLink = screen.getByRole('link', { name: /predictsmu home/i })
    expect(logoLink).toHaveAttribute('href', destination)
    expect(logoLink).toHaveTextContent('PredictSMU')
  })
})

/** A bar with two places to go and one control beside them, the shape the
 * signed-in navbar has: the control stands in for the account menu, which is
 * the next thing a Tab reaches after the last link. */
function renderTopBarWithNavigation() {
  render(
    <WithProviders user={trader}>
      <MemoryRouter initialEntries={['/markets']}>
        <TopBar
          nav={
            <>
              <Link to="/markets">Markets</Link>
              <Link to="/portfolio">Portfolio</Link>
            </>
          }
        >
          <button type="button">Account</button>
        </TopBar>
      </MemoryRouter>
    </WithProviders>,
  )
  return screen.getByRole('button', { name: 'Menu' })
}

// [FE] #438. Below the breakpoint the links sit behind this button. jsdom
// applies no stylesheet, so whether they are on screen is the e2e test's
// question (responsive-frame.e2e.ts); these pin what the button tells
// assistive technology and what the keyboard can do with it.
describe('TopBar — the navigation menu on a small screen', () => {
  it('starts closed and names the links it shows', () => {
    const menuButton = renderTopBarWithNavigation()

    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
    // aria-controls has to point at the element holding the links, or a
    // screen reader's "jump to controlled element" lands nowhere.
    const controlled = document.getElementById(menuButton.getAttribute('aria-controls') ?? '')
    expect(controlled).not.toBeNull()
    expect(within(controlled as HTMLElement).getByRole('link', { name: 'Portfolio' })).toBeInTheDocument()
  })

  it('opens and closes on the button', async () => {
    const actor = userEvent.setup()
    const menuButton = renderTopBarWithNavigation()

    await actor.click(menuButton)
    expect(menuButton).toHaveAttribute('aria-expanded', 'true')

    await actor.click(menuButton)
    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes on Escape and hands focus back to the button', async () => {
    const actor = userEvent.setup()
    const menuButton = renderTopBarWithNavigation()
    await actor.click(menuButton)
    await actor.tab()
    expect(screen.getByRole('link', { name: 'Markets' })).toHaveFocus()

    await actor.keyboard('{Escape}')

    // Focus was on a link that is about to be hidden; left there, the next
    // Tab would start from somewhere the trader cannot see.
    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
    expect(menuButton).toHaveFocus()
  })

  it('stays open while focus moves through its links, and closes once focus leaves them', async () => {
    const actor = userEvent.setup()
    const menuButton = renderTopBarWithNavigation()
    await actor.click(menuButton)

    await actor.tab()
    await actor.tab()
    expect(screen.getByRole('link', { name: 'Portfolio' })).toHaveFocus()
    expect(menuButton).toHaveAttribute('aria-expanded', 'true')

    await actor.tab()

    // Past the last link and onto the control beside the navigation: an open
    // panel left behind would cover the page the keyboard has moved on to.
    expect(screen.getByRole('button', { name: 'Account' })).toHaveFocus()
    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes when a link is chosen', async () => {
    const actor = userEvent.setup()
    const menuButton = renderTopBarWithNavigation()
    await actor.click(menuButton)

    // The link to the page already open changes no route, so this is the
    // case a "close when the address changes" rule would miss.
    await actor.click(screen.getByRole('link', { name: 'Markets' }))

    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes on a click elsewhere on the page', async () => {
    const actor = userEvent.setup()
    const menuButton = renderTopBarWithNavigation()
    await actor.click(menuButton)

    await actor.click(document.body)

    expect(menuButton).toHaveAttribute('aria-expanded', 'false')
  })
})
