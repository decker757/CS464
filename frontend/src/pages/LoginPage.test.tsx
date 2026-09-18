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

function renderLoginPage() {
  return render(
    <AuthContext.Provider value={{ user: null, login: mockLogin, logout: vi.fn() }}>
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    </AuthContext.Provider>
  )
}

describe('LoginPage — validation (unit)', () => {
  it('shows field errors without hitting the API when fields are empty', async () => {
    const user = userEvent.setup()
    renderLoginPage()

    await user.click(screen.getByRole('button', { name: /log in/i }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts.length).toBeGreaterThanOrEqual(1)
    expect(mockLogin).not.toHaveBeenCalled()
  })
})

describe('LoginPage — integration', () => {
  it('happy path: submits correct payload and navigates', async () => {
    const user = userEvent.setup()
    let capturedBody: unknown
    server.use(
      http.post('http://localhost:8000/auth/login', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json({ user: { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader' } })
      })
    )

    renderLoginPage()
    await user.type(screen.getByLabelText('Username or Email'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'test-fixture-pw-ok')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith(expect.objectContaining({ username: 'alice' })))
    expect(capturedBody).toEqual({ identifier: 'alice', password: 'test-fixture-pw-ok' })
    expect(mockNavigate).toHaveBeenCalledWith('/markets', { replace: true })
  })

  it('shows "Incorrect username or password." on invalid_credentials', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/login', () =>
        HttpResponse.json({ error: { code: 'invalid_credentials', message: 'Invalid credentials' } }, { status: 401 })
      )
    )

    renderLoginPage()
    await user.type(screen.getByLabelText('Username or Email'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'test-fixture-pw-bad')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect username or password.')
  })
})
