import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

const fillForm = async (
  user: ReturnType<typeof userEvent.setup>,
  overrides: Partial<Record<'username' | 'email' | 'password', string>> = {},
) => {
  await user.type(screen.getByLabelText('Username'), overrides.username ?? 'alice')
  await user.type(screen.getByLabelText('Email'), overrides.email ?? 'alice@smu.edu.sg')
  await user.type(screen.getByLabelText('Password'), overrides.password ?? 'test-fixture-pw-ok')
}

describe('RegisterPage — client-side validation', () => {
  it('blocks submission and shows error when password is too short', async () => {
    const user = userEvent.setup()
    renderRegisterPage()

    await fillForm(user, { password: 'short' })
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Password must be at least 12 characters.')
    expect(mockLogin).not.toHaveBeenCalled()
  })

  it('blocks submission and shows error when username contains spaces', async () => {
    const user = userEvent.setup()
    renderRegisterPage()

    await fillForm(user, { username: 'alice tan' })
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Username may only contain letters, numbers, hyphens and underscores.'
    )
    expect(mockLogin).not.toHaveBeenCalled()
  })

  it('blocks submission and shows error when email has no domain dot', async () => {
    const user = userEvent.setup()
    renderRegisterPage()

    await fillForm(user, { email: 'a@b' })
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Enter a valid email address.')
    expect(mockLogin).not.toHaveBeenCalled()
  })
})

describe('RegisterPage — integration', () => {
  it('happy path: sends correct payload, calls login, navigates to /markets', async () => {
    const user = userEvent.setup()
    let capturedBody: unknown
    server.use(
      http.post('http://localhost:8000/auth/register', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json(
          { user: { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' } },
          { status: 201 },
        )
      }),
    )

    renderRegisterPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() =>
      expect(mockLogin).toHaveBeenCalledWith(expect.objectContaining({ username: 'alice' })),
    )
    expect(capturedBody).toEqual({
      username: 'alice',
      email: 'alice@smu.edu.sg',
      password: 'test-fixture-pw-ok',
    })
    expect(mockNavigate).toHaveBeenCalledWith('/markets', { replace: true })
  })

  it('maps per-field errors when API returns duplicate_user', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/register', () =>
        HttpResponse.json(
          {
            error: {
              code: 'duplicate_user',
              message: 'Already registered',
              details: [{ field: 'username', message: 'Username already taken' }],
            },
          },
          { status: 409 },
        ),
      ),
    )

    renderRegisterPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Username already taken')
  })

  it('shows the server message under Email when pydantic rejects the address (422)', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/register', () =>
        HttpResponse.json(
          {
            detail: [
              {
                type: 'value_error',
                loc: ['body', 'email'],
                msg: 'value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.',
              },
            ],
          },
          { status: 422 },
        ),
      ),
    )

    renderRegisterPage()
    await fillForm(user, { email: 'me@test.local' })
    await user.click(screen.getByRole('button', { name: /create account/i }))

    // Scoped to the Email field rather than to any alert on the page. The
    // form has two places an error can surface — `Field`'s per-field <p
    // role="alert"> and the form-level <div role="alert"> above the inputs —
    // and a regression that sent a field error to the form-level one would
    // leave exactly one matching alert on screen, so an unscoped query would
    // still pass while "under Email" had stopped being true.
    await screen.findByRole('alert')
    const emailField = screen.getByLabelText('Email').closest('div')

    expect(emailField).not.toBeNull()
    expect(within(emailField as HTMLElement).getByRole('alert')).toHaveTextContent(
      'value is not a valid email address',
    )
  })

  it('shows generic error when the server is unreachable', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('http://localhost:8000/auth/register', () => HttpResponse.error()),
    )

    renderRegisterPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /create account/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong. Please try again.')
  })

  it('disables the button and shows spinner while the request is in flight', async () => {
    const user = userEvent.setup()
    let resolveRequest!: () => void
    server.use(
      http.post('http://localhost:8000/auth/register', () =>
        new Promise<Response>((res) => {
          resolveRequest = () =>
            res(
              HttpResponse.json(
                { user: { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' } },
                { status: 201 },
              ) as unknown as Response,
            )
        }),
      ),
    )

    renderRegisterPage()
    await fillForm(user)
    await user.click(screen.getByRole('button', { name: /create account/i }))

    const button = await screen.findByRole('button', { name: /creating account/i })
    expect(button).toBeDisabled()
    resolveRequest()
  })
})

describe('RegisterPage — already authenticated', () => {
  const trader = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader' as const, created_at: '2026-01-01' }

  beforeEach(() => mockNavigate.mockClear())

  it('redirects to /markets when already logged in', () => {
    render(
      <AuthContext.Provider value={{ user: trader, login: mockLogin, logout: vi.fn() }}>
        <MemoryRouter>
          <RegisterPage />
        </MemoryRouter>
      </AuthContext.Provider>,
    )
    expect(mockNavigate).toHaveBeenCalledWith('/markets', { replace: true })
  })
})
