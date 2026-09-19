import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { AuthProvider, useAuth } from './AuthContext'

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
})
