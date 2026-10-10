import { StrictMode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import AdminUsersPage from './AdminUsersPage'

const AUTH_BASE = 'http://localhost:8000'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

function adminUser(overrides: Record<string, unknown> = {}) {
  return {
    id: 'trader-1',
    username: 'alice',
    email: 'alice@smu.edu.sg',
    role: 'trader',
    is_suspended: false,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function mockUsers(users: Record<string, unknown>[], nextCursor: string | null = null) {
  server.use(
    http.get(`${AUTH_BASE}/admin/users`, () =>
      HttpResponse.json({ users, next_cursor: nextCursor, has_more: nextCursor !== null }),
    ),
  )
}

function renderPage() {
  return render(
    <StrictMode>
      <WithProviders user={admin}>
        <MemoryRouter initialEntries={['/admin/users']}>
          <Routes>
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/admin/users/:userId" element={<p>user history</p>} />
          </Routes>
        </MemoryRouter>
      </WithProviders>
    </StrictMode>,
  )
}

describe('AdminUsersPage', () => {
  it('shows a card for each user, with their username, email, role and suspension', async () => {
    mockUsers([
      adminUser({ id: 't1', username: 'alice', email: 'alice@smu.edu.sg' }),
      adminUser({ id: 't2', username: 'bob', email: 'bob@smu.edu.sg', is_suspended: true }),
    ])
    renderPage()

    expect(await screen.findByText('alice')).toBeInTheDocument()
    expect(screen.getByText('alice@smu.edu.sg')).toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
    expect(screen.getByText('Suspended')).toBeInTheDocument()
    expect(screen.queryAllByText('Suspended')).toHaveLength(1)
  })

  it('searches by username or email fragment', async () => {
    mockUsers([adminUser({ id: 't1', username: 'alice' }), adminUser({ id: 't2', username: 'bob', email: 'bob@smu.edu.sg' })])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    let sentQuery: string | null = null
    server.use(
      http.get(`${AUTH_BASE}/admin/users`, ({ request }) => {
        sentQuery = new URL(request.url).searchParams.get('q')
        return HttpResponse.json({ users: [adminUser({ id: 't1', username: 'alice' })], next_cursor: null, has_more: false })
      }),
    )

    await actor.type(screen.getByLabelText(/search by username or email/i), 'ali')
    await actor.click(screen.getByRole('button', { name: /search/i }))

    expect(await screen.findByText('alice')).toBeInTheDocument()
    expect(screen.queryByText('bob')).not.toBeInTheDocument()
    expect(sentQuery).toBe('ali')
  })

  it('shows a message naming the search when nothing matches', async () => {
    mockUsers([adminUser()])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    mockUsers([])
    await actor.type(screen.getByLabelText(/search by username or email/i), 'nobody')
    await actor.click(screen.getByRole('button', { name: /search/i }))

    expect(await screen.findByText(/no users match that search/i)).toBeInTheDocument()
  })

  it('shows an empty state when there are no users at all', async () => {
    mockUsers([])
    renderPage()
    expect(await screen.findByText(/no users yet/i)).toBeInTheDocument()
  })

  it('shows an error when the API fails', async () => {
    server.use(http.get(`${AUTH_BASE}/admin/users`, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('links to the selected user\'s history', async () => {
    mockUsers([adminUser({ id: 't1', username: 'alice' })])
    const actor = userEvent.setup()
    renderPage()

    await actor.click(await screen.findByText('alice'))

    expect(screen.getByText('user history')).toBeInTheDocument()
  })

  it('appends the next page when Load more is clicked', async () => {
    server.use(
      http.get(`${AUTH_BASE}/admin/users`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        return cursor === 'page-2'
          ? HttpResponse.json({ users: [adminUser({ id: 't2', username: 'bob' })], next_cursor: null, has_more: false })
          : HttpResponse.json({ users: [adminUser({ id: 't1', username: 'alice' })], next_cursor: 'page-2', has_more: true })
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')
    expect(screen.queryByText('bob')).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('bob')).toBeInTheDocument()
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })
})
