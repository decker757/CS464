import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext, AuthProvider } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import LoginPage from '../pages/LoginPage'
import GuestOnly from './GuestOnly'
import ProtectedRoute from './ProtectedRoute'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

/** The guard on its own, for what it decides. */
function renderGuestOnly(user: User | null | undefined, fromPath?: string) {
  const entry = fromPath
    ? { pathname: '/login', state: { from: { pathname: fromPath } } }
    : '/login'
  return render(
    <AuthContext.Provider value={{ user, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/login" element={<GuestOnly><p>guest content</p></GuestOnly>} />
          <Route path="/markets" element={<p>markets page</p>} />
          <Route path="/portfolio" element={<p>portfolio page</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

/** The guards as `App.tsx` mounts them, under the real provider, around a
 * protected route that is **not** `/markets`.
 *
 * `/portfolio` does not exist in App.tsx yet, and that is the point. While
 * `/markets` is the only protected route, `from` is always `/markets`, so a
 * test that used it passes just as happily against a hardcoded destination —
 * which is why this chain has now been broken twice with a green suite both
 * times. The default msw handlers already answer /auth/me with 401
 * `invalid_token` and /auth/login with alice, so this needs no overrides.
 */
function renderInApp(start: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[start]}>
        <Routes>
          <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
          <Route path="/markets" element={<ProtectedRoute><p>markets page</p></ProtectedRoute>} />
          <Route path="/portfolio" element={<ProtectedRoute><p>portfolio page</p></ProtectedRoute>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  )
}

const signIn = async (actor: ReturnType<typeof userEvent.setup>) => {
  await screen.findByLabelText('Username or Email')
  await actor.type(screen.getByLabelText('Username or Email'), 'alice')
  await actor.type(screen.getByLabelText('Password'), 'test-fixture-pw-ok')
  await actor.click(screen.getByRole('button', { name: /log in/i }))
}

describe('GuestOnly', () => {
  it('renders children while the session is still unknown', () => {
    // Not a loading screen. These pages are public, and making a guest wait on
    // /auth/me plus /auth/refresh before the landing page paints buys nothing.
    renderGuestOnly(undefined)
    expect(screen.getByText('guest content')).toBeInTheDocument()
  })

  it('renders children when nobody is signed in', () => {
    renderGuestOnly(null)
    expect(screen.getByText('guest content')).toBeInTheDocument()
  })

  it('redirects a signed-in user to /markets', () => {
    renderGuestOnly(trader)
    expect(screen.getByText('markets page')).toBeInTheDocument()
    expect(screen.queryByText('guest content')).not.toBeInTheDocument()
  })

  it('redirects a signed-in user to the page that sent them here', () => {
    renderGuestOnly(trader, '/portfolio')
    expect(screen.getByText('portfolio page')).toBeInTheDocument()
    expect(screen.queryByText('markets page')).not.toBeInTheDocument()
  })
})

describe('GuestOnly — signing in', () => {
  it('lands a new session on the page that triggered the login', async () => {
    const actor = userEvent.setup()
    // Signed out at a protected route: ProtectedRoute bounces to /login and
    // records where the visitor was going.
    renderInApp('/portfolio')

    await signIn(actor)

    await waitFor(() => expect(screen.getByText('portfolio page')).toBeInTheDocument())
    expect(screen.queryByText('markets page')).not.toBeInTheDocument()
  })

  it('lands on /markets when the visitor came to /login directly', async () => {
    const actor = userEvent.setup()
    renderInApp('/login')

    await signIn(actor)

    await waitFor(() => expect(screen.getByText('markets page')).toBeInTheDocument())
  })
})
