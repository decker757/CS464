import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import { server } from '../test/server'
import LoginPage from './LoginPage'

const mockLogin = vi.fn()
const mockNavigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function renderLoginPage(fromPath?: string) {
  const initialEntry = fromPath
    ? { pathname: '/login', state: { from: { pathname: fromPath } } }
    : '/login'
  return render(
    <AuthContext.Provider value={{ user: null, login: mockLogin, logout: vi.fn() }}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <LoginPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('LoginPage — client-side validation', () => {
  it('shows exactly 2 field errors when both fields are empty', async () => {
    const user = userEvent.setup()
    renderLoginPage()

    await user.click(screen.getByRole('button', { name: /log in/i }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(2)
    expect(mockLogin).not.toHaveBeenCalled()
  })
})

describe('LoginPage — integration', () => {
  it('happy path: sends correct payload, calls login, navigates to /markets', async () => {
    const user = userEvent.setup()
    let capturedBody: unknown
    server.use(
      http.post('http://localhost:8000/auth/login', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json({
          user: { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' },
        })
      }),
    )

    renderLoginPage()
    await user.type(screen.getByLabelText('Username or Email'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'test-fixture-pw-ok')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    await waitFor(() =>
      expect(mockLogin).toHaveBeenCalledWith(expect.objectContaining({ username: 'alice' })),
    )
    expect(capturedBody).toEqual({ identifier: 'alice', password: 'test-fixture-pw-ok' })
    expect(mockNavigate).toHaveBeenCalledWith('/markets', { replace: true })
  })

  it('redirects to the page that triggered the login', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/login', () =>
        HttpResponse.json({
          user: { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' },
        }),
      ),
    )

    renderLoginPage('/portfolio')
    await user.type(screen.getByLabelText('Username or Email'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'test-fixture-pw-ok')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith('/portfolio', { replace: true }),
    )
  })

  it('shows "Incorrect username or password." for invalid_credentials', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/login', () =>
        HttpResponse.json(
          { error: { code: 'invalid_credentials', message: 'Invalid credentials' } },
          { status: 401 },
        ),
      ),
    )

    renderLoginPage()
    await user.type(screen.getByLabelText('Username or Email'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'test-fixture-pw-bad')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect username or password.')
  })

  it('shows the server message for other error codes (e.g. account_suspended)', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/login', () =>
        HttpResponse.json(
          { error: { code: 'account_suspended', message: 'Your account has been suspended.' } },
          { status: 403 },
        ),
      ),
    )

    renderLoginPage()
    await user.type(screen.getByLabelText('Username or Email'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'test-fixture-pw-ok')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Your account has been suspended.')
  })

  it('shows generic error when the server is unreachable', async () => {
    const user = userEvent.setup()
    server.use(http.post('http://localhost:8000/auth/login', () => HttpResponse.error()))

    renderLoginPage()
    await user.type(screen.getByLabelText('Username or Email'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'test-fixture-pw-ok')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
  })
})
