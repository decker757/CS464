import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { Route, Routes, MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import { server } from '../test/server'
import RegisterPage from './RegisterPage'

const mockLogin = vi.fn()
const mockNavigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function renderRegisterPage() {
  return render(
    <AuthContext.Provider value={{ user: null, login: mockLogin, logout: vi.fn() }}>
      <MemoryRouter>
        <RegisterPage />
      </MemoryRouter>
    </AuthContext.Provider>
  )
}

const fillForm = async (user: ReturnType<typeof userEvent.setup>, overrides: Partial<Record<'username' | 'email' | 'password', string>> = {}) => {
  await user.type(screen.getByLabelText('Username'), overrides.username ?? 'alice')
  await user.type(screen.getByLabelText('Email'), overrides.email ?? 'alice@smu.edu.sg')
  await user.type(screen.getByLabelText('Password'), overrides.password ?? 'supersecret123')
}

describe('RegisterPage — validation (unit)', () => {
  it('shows a password error and does not call the API when password is too short', async () => {
    const user = userEvent.setup()
    renderRegisterPage()

    await fillForm(user, { password: 'short' })
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/12 characters/)
    expect(mockLogin).not.toHaveBeenCalled()
  })

  it('shows a username error when username contains spaces', async () => {
    const user = userEvent.setup()
    renderRegisterPage()

    await fillForm(user, { username: 'alice tan' })
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/letters, numbers/)
  })
})

describe('RegisterPage — integration', () => {
  it('happy path: submits correct payload and navigates to /markets', async () => {
    const user = userEvent.setup()
    let capturedBody: unknown
    server.use(
      http.post('http://localhost:8000/auth/register', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json({ user: { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader' } }, { status: 201 })
      })
    )

    renderRegisterPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => expect(mockLogin).toHaveBeenCalledWith(expect.objectContaining({ username: 'alice' })))
    expect(capturedBody).toEqual({ username: 'alice', email: 'alice@smu.edu.sg', password: 'supersecret123' })
    expect(mockNavigate).toHaveBeenCalledWith('/markets', { replace: true })
  })

  it('shows per-field error when API returns duplicate_user', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/register', () =>
        HttpResponse.json(
          { error: { code: 'duplicate_user', message: 'Already registered', details: [{ field: 'username', message: 'Username already taken' }] } },
          { status: 409 }
        )
      )
    )

    renderRegisterPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Username already taken')
  })

  it('shows loading state while the request is in flight', async () => {
    const user = userEvent.setup()
    let resolve!: () => void
    server.use(
      http.post('http://localhost:8000/auth/register', () =>
        new Promise<Response>((res) => { resolve = () => res(HttpResponse.json({ user: { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader' } }, { status: 201 }) as unknown as Response) })
      )
    )

    renderRegisterPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByText(/creating account/i)).toBeInTheDocument()
    resolve()
  })
})
