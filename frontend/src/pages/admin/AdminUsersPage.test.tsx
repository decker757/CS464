import { StrictMode } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import AdminUsersPage from './AdminUsersPage'

// Renders the username the router state named, so a test can prove the link
// actually carries the user along, not only that the route matched.
function UserHistoryStub() {
  const location = useLocation()
  const user = (location.state as { user?: { username: string } } | null)?.user
  return <p>user history for {user?.username ?? 'nobody'}</p>
}

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

// What the page asked GET /admin/users for, one entry per request, so a test
// can read the parameters of the latest one.
interface SentParams {
  q: string | null
  role: string | null
  is_suspended: string | null
  cursor: string | null
}

function recordRequests(usersFor: (sent: SentParams) => Record<string, unknown>[]): SentParams[] {
  const sent: SentParams[] = []
  server.use(
    http.get(`${AUTH_BASE}/admin/users`, ({ request }) => {
      const params = new URL(request.url).searchParams
      const entry = {
        q: params.get('q'),
        role: params.get('role'),
        is_suspended: params.get('is_suspended'),
        cursor: params.get('cursor'),
      }
      sent.push(entry)
      return HttpResponse.json({ users: usersFor(entry), next_cursor: null, has_more: false })
    }),
  )
  return sent
}

function renderPage() {
  return render(
    <StrictMode>
      <WithProviders user={admin}>
        <MemoryRouter initialEntries={['/admin/users']}>
          <Routes>
            <Route path="/admin/users" element={<AdminUsersPage />} />
            <Route path="/admin/users/:userId" element={<UserHistoryStub />} />
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

  it.each([
    ['Filter by role', 'Admins only', { role: 'admin', is_suspended: null }],
    ['Filter by role', 'Traders only', { role: 'trader', is_suspended: null }],
    ['Filter by status', 'Suspended only', { role: null, is_suspended: 'true' }],
    ['Filter by status', 'Active only', { role: null, is_suspended: 'false' }],
  ])('%s: %s asks the server for exactly that, and shows what it returns', async (group, pill, expected) => {
    mockUsers([adminUser({ id: 't1', username: 'alice' })])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    const sent = recordRequests(() => [adminUser({ id: 'a1', username: 'carol', role: 'admin', is_suspended: true })])
    await actor.click(within(screen.getByRole('radiogroup', { name: group })).getByRole('radio', { name: pill }))

    expect(await screen.findByText('carol')).toBeInTheDocument()
    expect(screen.queryByText('alice')).not.toBeInTheDocument()
    expect(sent.at(-1)).toEqual({ q: null, cursor: null, ...expected })
  })

  it('combines both filters with the search, and shows the chosen pill as checked', async () => {
    mockUsers([adminUser({ id: 't1', username: 'alice' })])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    const sent = recordRequests(() => [adminUser({ id: 'a1', username: 'carol', role: 'admin', is_suspended: true })])
    await actor.type(screen.getByLabelText(/search by username or email/i), 'car')
    await actor.click(screen.getByRole('button', { name: /search/i }))
    const roles = within(screen.getByRole('radiogroup', { name: 'Filter by role' }))
    await actor.click(roles.getByRole('radio', { name: 'Admins only' }))
    await actor.click(within(screen.getByRole('radiogroup', { name: 'Filter by status' })).getByRole('radio', { name: 'Suspended only' }))

    expect(await screen.findByText('carol')).toBeInTheDocument()
    expect(sent.at(-1)).toEqual({ q: 'car', role: 'admin', is_suspended: 'true', cursor: null })
    expect(roles.getByRole('radio', { name: 'Admins only' })).toBeChecked()
    expect(roles.getByRole('radio', { name: 'Any role' })).not.toBeChecked()
  })

  it('goes back to every role when Any role is chosen again', async () => {
    mockUsers([adminUser({ id: 't1', username: 'alice' })])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    const sent = recordRequests(sentParams =>
      sentParams.role === 'admin'
        ? [adminUser({ id: 'a1', username: 'carol', role: 'admin' })]
        : [adminUser({ id: 't1', username: 'alice' }), adminUser({ id: 'a1', username: 'carol', role: 'admin' })],
    )
    const roles = within(screen.getByRole('radiogroup', { name: 'Filter by role' }))
    await actor.click(roles.getByRole('radio', { name: 'Admins only' }))
    await screen.findByText('carol')
    await actor.click(roles.getByRole('radio', { name: 'Any role' }))

    expect(await screen.findByText('alice')).toBeInTheDocument()
    expect(sent.at(-1)).toEqual({ q: null, role: null, is_suspended: null, cursor: null })
  })

  it('moves the role with the arrow keys, as a radio group does', async () => {
    mockUsers([adminUser({ id: 't1', username: 'alice' })])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    const sent = recordRequests(sentParams =>
      sentParams.role === 'admin' ? [adminUser({ id: 'a1', username: 'carol', role: 'admin' })] : [adminUser({ id: 't1', username: 'alice' })],
    )
    const roles = within(screen.getByRole('radiogroup', { name: 'Filter by role' }))
    roles.getByRole('radio', { name: 'Any role' }).focus()
    await actor.keyboard('{ArrowRight}')

    expect(await screen.findByText('carol')).toBeInTheDocument()
    expect(roles.getByRole('radio', { name: 'Admins only' })).toBeChecked()
    expect(roles.getByRole('radio', { name: 'Admins only' })).toHaveFocus()
    expect(sent.at(-1)).toEqual({ q: null, role: 'admin', is_suspended: null, cursor: null })

    await actor.keyboard('{ArrowLeft}')

    expect(await screen.findByText('alice')).toBeInTheDocument()
    expect(roles.getByRole('radio', { name: 'Any role' })).toBeChecked()
    expect(sent.at(-1)).toEqual({ q: null, role: null, is_suspended: null, cursor: null })
  })

  it('reaches only the checked pill of a group with Tab, as a radio group does', async () => {
    mockUsers([adminUser({ id: 't1', username: 'alice' })])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    screen.getByRole('button', { name: /search/i }).focus()
    await actor.tab()
    expect(screen.getByRole('radio', { name: 'Any role' })).toHaveFocus()
    await actor.tab()
    expect(screen.getByRole('radio', { name: 'Any status' })).toHaveFocus()
  })

  it('starts the list again from page one when a filter changes', async () => {
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
    await actor.click(screen.getByRole('button', { name: /load more/i }))
    await screen.findByText('bob')

    const sent = recordRequests(() => [adminUser({ id: 'a1', username: 'carol', role: 'admin' })])
    await actor.click(within(screen.getByRole('radiogroup', { name: 'Filter by role' })).getByRole('radio', { name: 'Admins only' }))

    expect(await screen.findByText('carol')).toBeInTheDocument()
    expect(screen.queryByText('alice')).not.toBeInTheDocument()
    expect(screen.queryByText('bob')).not.toBeInTheDocument()
    expect(sent.at(-1)).toEqual({ q: null, role: 'admin', is_suspended: null, cursor: null })
  })

  it('says no users match the filters, not that there are no users, when a filter finds nobody', async () => {
    mockUsers([adminUser()])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('alice')

    mockUsers([])
    await actor.click(within(screen.getByRole('radiogroup', { name: 'Filter by status' })).getByRole('radio', { name: 'Suspended only' }))

    expect(await screen.findByText('No users match these filters.')).toBeInTheDocument()
    expect(screen.queryByText(/no users yet/i)).not.toBeInTheDocument()
  })

  it('links to the selected user\'s history, carrying the user along as router state', async () => {
    mockUsers([adminUser({ id: 't1', username: 'alice' })])
    const actor = userEvent.setup()
    renderPage()

    await actor.click(await screen.findByText('alice'))

    expect(screen.getByText('user history for alice')).toBeInTheDocument()
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
