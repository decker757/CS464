import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import * as authApi from '../api/authApi'
import { server } from '../test/server'
import { AuthProvider, useAuth, type User } from './AuthContext'

function DisplayUser() {
  const { user, logout } = useAuth()
  if (user === undefined) return <p>loading</p>
  if (!user) return <p>logged out</p>
  return (
    <>
      <p>logged in as {user.username}</p>
      <button onClick={logout}>Log out</button>
    </>
  )
}

function renderWithProvider() {
  return render(
    <AuthProvider>
      <DisplayUser />
    </AuthProvider>,
  )
}

describe('AuthContext', () => {
  it('shows loading state before /auth/me resolves', () => {
    // The default handler returns 401, but we need to intercept before it responds
    server.use(
      http.get('http://localhost:8000/auth/me', () => new Promise(() => {})), // never resolves
    )
    renderWithProvider()
    expect(screen.getByText('loading')).toBeInTheDocument()
  })

  it('shows logged-out state when /auth/me returns 401', async () => {
    // default handler already returns 401
    renderWithProvider()
    expect(await screen.findByText('logged out')).toBeInTheDocument()
  })

  it('shows logged-in state when /auth/me returns a user', async () => {
    server.use(
      http.get('http://localhost:8000/auth/me', () =>
        HttpResponse.json({ id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }),
      ),
    )
    renderWithProvider()
    expect(await screen.findByText('logged in as alice')).toBeInTheDocument()
  })

  it('clears the user when logout is called', async () => {
    server.use(
      http.get('http://localhost:8000/auth/me', () =>
        HttpResponse.json({ id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }),
      ),
      http.post('http://localhost:8000/auth/logout', () => new HttpResponse(null, { status: 204 })),
    )

    const user = userEvent.setup()
    renderWithProvider()
    await screen.findByText('logged in as alice')
    await user.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() => expect(screen.getByText('logged out')).toBeInTheDocument())
  })

  describe('a login that lands while /auth/me is still in flight', () => {
    // The real /login and /register are rendered outside ProtectedRoute in
    // App.tsx, so both forms are usable for as long as the bootstrap request
    // takes — and an unauthenticated /auth/me takes a whole /auth/refresh
    // round trip to fail. This is what that screen is.
    function SignInScreen({ as }: { as: User }) {
      const { user, login } = useAuth()
      return (
        <>
          <button onClick={() => login(as)}>Sign in</button>
          {user === undefined && <p>loading</p>}
          {user === null && <p>logged out</p>}
          {user && <p>logged in as {user.username}</p>}
        </>
      )
    }

    /** A /auth/me that hangs until the test decides how it ends. */
    function pendingLookup() {
      let send!: (response: Response) => void
      const promise = new Promise<Response>((resolve) => {
        send = resolve
      })
      server.use(http.get('http://localhost:8000/auth/me', () => promise))
      // Returns once the lookup has been answered *and* anything it was going
      // to do about it has had time to happen. `act` because the state update
      // that follows is one React is not expecting.
      return async (response: Response) => {
        await act(async () => {
          send(response)
          await new Promise((resolve) => setTimeout(resolve, 50))
        })
      }
    }

    const alice: User = {
      id: '1', username: 'alice', email: 'alice@smu.edu.sg',
      role: 'trader', created_at: '2026-01-01',
    }
    const bob: User = {
      id: '2', username: 'bob', email: 'bob@smu.edu.sg',
      role: 'trader', created_at: '2026-01-01',
    }

    it('keeps the user when the lookup fails after they signed in', async () => {
      const finish = pendingLookup()
      const person = userEvent.setup()
      render(
        <AuthProvider>
          <SignInScreen as={alice} />
        </AuthProvider>,
      )
      await screen.findByText('loading')

      await person.click(screen.getByRole('button', { name: /sign in/i }))
      expect(await screen.findByText('logged in as alice')).toBeInTheDocument()

      // The lookup now fails, as it was always going to — nobody was signed in
      // at the moment it was sent. That is stale news, not a logout.
      await finish(
        HttpResponse.json(
          { error: { code: 'invalid_token', message: 'Not authenticated.' } },
          { status: 401 },
        ),
      )

      // Polled rather than read once: the bug is a state update arriving
      // late, so a test that looks only at this instant can pass by being
      // early rather than by being right.
      await expect(
        screen.findByText('logged out', {}, { timeout: 400 }),
      ).rejects.toThrow()
      expect(screen.getByText('logged in as alice')).toBeInTheDocument()
    })

    it('keeps the user when the lookup succeeds as somebody else', async () => {
      // The mirror case, and the reason both branches are guarded rather than
      // just the failing one: a session that belonged to a previous account on
      // this browser must not replace the person who just signed in.
      const finish = pendingLookup()
      const person = userEvent.setup()
      render(
        <AuthProvider>
          <SignInScreen as={bob} />
        </AuthProvider>,
      )
      await screen.findByText('loading')

      await person.click(screen.getByRole('button', { name: /sign in/i }))
      expect(await screen.findByText('logged in as bob')).toBeInTheDocument()

      await finish(HttpResponse.json(alice))

      await expect(
        screen.findByText('logged in as alice', {}, { timeout: 400 }),
      ).rejects.toThrow()
      expect(screen.getByText('logged in as bob')).toBeInTheDocument()
    })
  })
})

describe('AuthContext — a session that ends while the app is open', () => {
  // [FE] #353. The access token expires, the refresh token with it, and the
  // interceptor's /auth/refresh comes back refused. Nothing used to clear the
  // signed-in user, so every page failed while the navbar still had a name on
  // it and no guard ever sent anybody to /login.
  const alice: User = {
    id: '1', username: 'alice', email: 'alice@smu.edu.sg',
    role: 'trader', created_at: '2026-01-01',
  }

  const notAuthenticated = () =>
    HttpResponse.json(
      { error: { code: 'invalid_token', message: 'Not authenticated.' } },
      { status: 401 },
    )

  /** A page doing what every page does: asking the server for something. */
  function Screen() {
    const { user, hasSessionExpired } = useAuth()
    return (
      <>
        <button onClick={() => void authApi.getCurrentUser().catch(() => {})}>
          Load the page
        </button>
        {user === undefined && <p>loading</p>}
        {user === null && <p>logged out</p>}
        {user && <p>logged in as {user.username}</p>}
        {hasSessionExpired && <p>the session expired</p>}
      </>
    )
  }

  /** /auth/me answers with alice, except on the call numbered `refusedCall`. */
  function refuseMeOnCall(refusedCall: number) {
    let calls = 0
    server.use(
      http.get('http://localhost:8000/auth/me', () => {
        calls++
        return calls === refusedCall ? notAuthenticated() : HttpResponse.json(alice)
      }),
    )
  }

  /** Signs in through the bootstrap lookup, then has the page send a request. */
  async function signInThenLoadThePage() {
    const person = userEvent.setup()
    render(
      <AuthProvider>
        <Screen />
      </AuthProvider>,
    )
    await screen.findByText('logged in as alice')
    await person.click(screen.getByRole('button', { name: /load the page/i }))
  }

  it('logs the user out when the refresh is refused', async () => {
    // The bootstrap call succeeds; the page's own call is the one refused, and
    // the default handler refuses the /auth/refresh that follows it.
    refuseMeOnCall(2)

    await signInThenLoadThePage()

    expect(await screen.findByText('logged out')).toBeInTheDocument()
    expect(screen.getByText('the session expired')).toBeInTheDocument()
  })

  it('keeps the user signed in when the refresh works', async () => {
    refuseMeOnCall(2)
    server.use(
      http.post('http://localhost:8000/auth/refresh', () =>
        new HttpResponse(null, { status: 204 }),
      ),
    )

    await signInThenLoadThePage()

    // Polled rather than read once: a single 401 the refresh resolved must not
    // end a session, and a test that looks only at this instant can pass by
    // being early rather than by being right.
    await expect(
      screen.findByText('logged out', {}, { timeout: 400 }),
    ).rejects.toThrow()
    expect(screen.getByText('logged in as alice')).toBeInTheDocument()
    expect(screen.queryByText('the session expired')).not.toBeInTheDocument()
  })

  it('does not claim a session expired when nobody was signed in', async () => {
    // The default handlers: /auth/me refused, and the refresh the interceptor
    // sends after it refused too. That is every first visit by a guest, and
    // telling them their session expired would be a lie.
    render(
      <AuthProvider>
        <Screen />
      </AuthProvider>,
    )

    expect(await screen.findByText('logged out')).toBeInTheDocument()
    expect(screen.queryByText('the session expired')).not.toBeInTheDocument()
  })
})
