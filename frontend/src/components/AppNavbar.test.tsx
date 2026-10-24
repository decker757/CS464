import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthProvider } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { BalanceProvider } from '../context/BalanceContext'
import { holdUntilReleased } from '../test/holdUntilReleased'
import { server } from '../test/server'
import { WithProviders } from '../test/renderWithProviders'
import AppNavbar from './AppNavbar'
import ProtectedRoute from './ProtectedRoute'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }
const admin: User = { id: '2', username: 'bob', email: 'bob@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

/** The navbar on its own, for what it renders. `balance` defaults to
 * undefined (still loading); pass it to test the loaded and failed states. */
function renderNavbar(user: User | null | undefined, balance?: string | null) {
  render(
    <WithProviders user={user} balance={balance}>
      <MemoryRouter initialEntries={['/markets']}>
        <AppNavbar />
      </MemoryRouter>
    </WithProviders>,
  )
}

/** The navbar as `App.tsx` mounts it: behind the route guard, under the real
 * provider.
 *
 * Worth the extra setup, because the two things a stub leaves out are the two
 * things logging out touches. A `logout` mock never clears `user`, so nothing
 * re-renders; and without `ProtectedRoute` there is no guard to react when it
 * does. A test built without them asserts a destination the real app never
 * reaches, and keeps passing for as long as that is wrong.
 */
function renderInApp() {
  server.use(
    http.get('http://localhost:8000/auth/me', () => HttpResponse.json(trader)),
    http.post('http://localhost:8000/auth/logout', () => HttpResponse.json({ message: 'Logged out.' })),
    http.get('http://localhost:8003/ledger/balances/me', () =>
      HttpResponse.json({
        user_id: trader.id, account_id: 'acc-1',
        // `available` is what the navbar shows: the two differ whenever a
        // resting buy order holds credits (ADR 0024).
        balance: '1000.0000', reserved: '0.0000', available: '1000.0000',
      }),
    ),
  )
  render(
    <AuthProvider>
      <BalanceProvider>
        <MemoryRouter initialEntries={['/markets']}>
          <Routes>
            <Route
              path="/markets"
              element={
                <ProtectedRoute>
                  <AppNavbar />
                </ProtectedRoute>
              }
            />
            <Route path="/" element={<p>landing page</p>} />
            <Route path="/login" element={<p>login page</p>} />
          </Routes>
        </MemoryRouter>
      </BalanceProvider>
    </AuthProvider>,
  )
}

/** Opens the account cluster's menu, which is where Log Out lives (#437).
 * The button carries the username, so that is its accessible name. */
async function openAccountMenu(actor: ReturnType<typeof userEvent.setup>, username: string) {
  await actor.click(screen.getByRole('button', { name: username }))
}

describe('AppNavbar', () => {
  it('shows the brand name', () => {
    renderNavbar(trader)
    expect(screen.getByText('PredictSMU')).toBeInTheDocument()
  })

  it('shows the logged-in username', () => {
    renderNavbar(trader)
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('shows the Leaderboard link to any signed-in user', () => {
    // Any valid access token sees the same board (ledger-service.md) — no
    // role gate, unlike New Market below.
    renderNavbar(trader)
    expect(screen.getByRole('link', { name: /leaderboard/i })).toHaveAttribute('href', '/leaderboard')
  })

  it('shows the New Market link for admins', () => {
    renderNavbar(admin)
    expect(screen.getByRole('link', { name: /new market/i })).toBeInTheDocument()
  })

  it('does not show the New Market link for traders', () => {
    renderNavbar(trader)
    expect(screen.queryByRole('link', { name: /new market/i })).not.toBeInTheDocument()
  })

  it('offers no logout control when nobody is signed in', () => {
    // The brand half of the navbar is public; the control is not. Anywhere
    // this component is used outside a ProtectedRoute — a public market page,
    // say — a signed-out visitor must not be handed a Log Out button.
    renderNavbar(null)

    expect(screen.getByText('PredictSMU')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()
  })

  it('shows nothing for the balance while it is still loading', () => {
    // undefined — the renderNavbar default — must not be confused with a
    // failed load; it should simply not render yet.
    renderNavbar(trader)
    expect(screen.queryByLabelText('available balance')).not.toBeInTheDocument()
  })

  it('shows Balance unavailable when the load has failed', () => {
    renderNavbar(trader, null)
    expect(screen.getByText('Balance unavailable')).toBeInTheDocument()
  })

  it('shows what is available to spend, not the balance a resting order has reserved part of', () => {
    // ADR 0024: a resting buy order's credits stay in `balance` but cannot
    // be spent, so the figure under "available balance" has to be
    // `available`. Showing `balance` here would overstate it by exactly what
    // the trader's own orders hold.
    render(
      <WithProviders user={trader} balance="1000.0000" available="985.0000">
        <MemoryRouter>
          <AppNavbar />
        </MemoryRouter>
      </WithProviders>,
    )

    expect(screen.getByLabelText('available balance')).toHaveTextContent('985 credits')
  })

  it('shows the real balance fetched through BalanceProvider, not just a stubbed one', async () => {
    renderInApp()
    // The figure and its unit are two nodes now, so this reads the readout by
    // its label rather than looking for one text node holding both.
    expect(await screen.findByLabelText('available balance')).toHaveTextContent('1,000 credits')
  })

  it('does not offer the balance as somewhere to go', () => {
    // It used to carry the active link's exact two classes, and the username
    // an inactive link's, so the row read as seven navigation items of which
    // two did nothing when clicked (#437). Neither is in the navigation
    // landmark any more, and neither is a link.
    renderNavbar(trader, '1000.0000')
    const navigation = screen.getByRole('navigation')

    expect(within(navigation).queryByLabelText('available balance')).not.toBeInTheDocument()
    expect(within(navigation).queryByText('alice')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /credits/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'alice' })).not.toBeInTheDocument()
  })
})

// Log Out sits in the account menu rather than in the bar, which is why each
// of these opens the menu first (#437).
describe('AppNavbar — logging out', () => {
  it('ends the session', async () => {
    const actor = userEvent.setup()
    renderInApp()
    await screen.findByText('alice')

    await openAccountMenu(actor, 'alice')
    await actor.click(screen.getByRole('button', { name: /log out/i }))

    // `logout` clears `user`, which unmounts this navbar (it lives under
    // ProtectedRoute) in favour of ProtectedRoute's own <Navigate to="/login">.
    // That is the only redirect authority for this transition — AppNavbar no
    // longer calls navigate() itself, so there is nothing left to race.
    await waitFor(() => expect(screen.getByText('login page')).toBeInTheDocument())
    expect(screen.queryByText('alice')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()
  })

  it('ends the session even when the request fails', async () => {
    const actor = userEvent.setup()
    renderInApp()
    server.use(http.post('http://localhost:8000/auth/logout', () => HttpResponse.error()))
    await screen.findByText('alice')

    await openAccountMenu(actor, 'alice')
    await actor.click(screen.getByRole('button', { name: /log out/i }))

    // Logging out is the one action a user has to be able to trust, and the
    // endpoint is unauthenticated and always succeeds server-side, so a
    // rejection here is the network rather than a refusal. Leaving the session
    // up because the network blinked — with a button that appears to do
    // nothing — is the wrong way to fail.
    await waitFor(() => expect(screen.queryByText('alice')).not.toBeInTheDocument())
  })

  it('disables the control while the request is in flight', async () => {
    const { held, release } = holdUntilReleased()
    const actor = userEvent.setup()
    renderInApp()
    server.use(
      http.post('http://localhost:8000/auth/logout', async () => {
        await held
        return HttpResponse.json({ message: 'Logged out.' })
      }),
    )
    await screen.findByText('alice')

    await openAccountMenu(actor, 'alice')
    await actor.click(screen.getByRole('button', { name: /log out/i }))

    // A second click during the round trip would send a second POST and a
    // second navigation, interleaving with the guard's own redirect in an
    // order nothing controls. Every other request-sending button in this app
    // disables itself the same way.
    const button = screen.getByRole('button', { name: /signing out/i })
    expect(button).toBeDisabled()

    release()
    await waitFor(() => expect(screen.queryByText('alice')).not.toBeInTheDocument())
  })
})

describe('AppNavbar — finding your way around', () => {
  it('puts Markets first in the signed-in navigation, for both roles', () => {
    // [X-1]–[X-4] shipped the market list and then left it out of the nav, so
    // from Portfolio or History the only way back was the browser's Back
    // button. "All Markets" is the admin list and is not a substitute, which
    // is why this has to hold for an admin too.
    for (const user of [trader, admin]) {
      renderNavbar(user)
      const links = within(screen.getByRole('navigation')).getAllByRole('link')
      // The landmark holds the navigation and nothing else, so the first link
      // in it is the first nav item: the wordmark and the account cluster are
      // outside it (#437).
      expect(links[0]).toHaveAccessibleName('Markets')
      cleanup()
    }
  })

  it('marks the page you are on, and only that one', () => {
    // Colour alone does not say "you are here" to a screen reader; NavLink
    // sets aria-current="page" on the match, which is what carries it.
    renderNavbar(trader)

    expect(screen.getByRole('link', { name: 'Markets' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Portfolio' })).not.toHaveAttribute('aria-current')
  })

  it('keeps a market detail page inside the Markets section', () => {
    // /markets/:id is still Markets; an exact match would unmark the nav the
    // moment a trader opened a market, which is most of the time they spend.
    render(
      <WithProviders user={trader}>
        <MemoryRouter initialEntries={['/markets/abc-123']}>
          <AppNavbar />
        </MemoryRouter>
      </WithProviders>,
    )

    expect(screen.getByRole('link', { name: 'Markets' })).toHaveAttribute('aria-current', 'page')
  })

  it('does not mark Markets when an admin is on the admin market list', () => {
    // /admin/markets is a different list. Prefix matching that treated it as
    // /markets would light up both.
    render(
      <WithProviders user={admin}>
        <MemoryRouter initialEntries={['/admin/markets']}>
          <AppNavbar />
        </MemoryRouter>
      </WithProviders>,
    )

    expect(screen.getByRole('link', { name: 'Markets' })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('link', { name: 'All Markets' })).toHaveAttribute('aria-current', 'page')
  })
})
