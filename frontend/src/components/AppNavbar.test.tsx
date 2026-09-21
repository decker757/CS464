import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthContext, AuthProvider } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { server } from '../test/server'
import AppNavbar from './AppNavbar'
import ProtectedRoute from './ProtectedRoute'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

/** The navbar on its own, for what it renders. */
function renderNavbar(user: User | null | undefined) {
  render(
    <AuthContext.Provider value={{ user, login: () => {}, logout: vi.fn() }}>
      <MemoryRouter initialEntries={['/markets']}>
        <AppNavbar />
      </MemoryRouter>
    </AuthContext.Provider>,
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
    http.post('http://localhost:8000/auth/logout', () => new HttpResponse(null, { status: 204 })),
  )
  render(
    <AuthProvider>
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
    </AuthProvider>,
  )
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

  it('offers no logout control when nobody is signed in', () => {
    // The brand half of the navbar is public; the control is not. Anywhere
    // this component is used outside a ProtectedRoute — a public market page,
    // say — a signed-out visitor must not be handed a Log Out button.
    renderNavbar(null)

    expect(screen.getByText('PredictSMU')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /log out/i })).not.toBeInTheDocument()
  })
})

describe('AppNavbar — logging out', () => {
  it('ends the session', async () => {
    const actor = userEvent.setup()
    renderInApp()
    await screen.findByText('alice')

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

    await actor.click(screen.getByRole('button', { name: /log out/i }))

    // Logging out is the one action a user has to be able to trust, and the
    // endpoint is unauthenticated and always succeeds server-side, so a
    // rejection here is the network rather than a refusal. Leaving the session
    // up because the network blinked — with a button that appears to do
    // nothing — is the wrong way to fail.
    await waitFor(() => expect(screen.queryByText('alice')).not.toBeInTheDocument())
  })

  it('disables the control while the request is in flight', async () => {
    let release!: () => void
    const pending = new Promise<Response>((resolve) => {
      release = () => resolve(new HttpResponse(null, { status: 204 }))
    })
    const actor = userEvent.setup()
    renderInApp()
    server.use(http.post('http://localhost:8000/auth/logout', () => pending))
    await screen.findByText('alice')

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
