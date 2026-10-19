import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { holdUntilReleased } from '../../test/holdUntilReleased'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import AdminUserHistoryPage from './AdminUserHistoryPage'

const LEDGER_BASE = 'http://localhost:8003'
const MARKET_BASE = 'http://localhost:8001'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

function entry(overrides: Record<string, unknown> = {}) {
  return {
    id: 'e1',
    created_at: '2026-09-15T09:41:02.118000Z',
    amount: '-2.5313',
    balance_after: '997.4687',
    transaction_id: 't1',
    kind: 'trade_buy',
    context: {},
    market_id: 'mkt-1',
    outcome_id: 'o-yes',
    side: 'buy',
    quantity: '5.0000',
    average_price: '0.5063',
    ...overrides,
  }
}

function market(overrides: Record<string, unknown> = {}) {
  return {
    id: 'mkt-1',
    status: 'open',
    question: 'Will it rain in Singapore tomorrow?',
    description: null,
    outcomes: [
      { id: 'o-yes', position: 0, label: 'Yes' },
      { id: 'o-no', position: 1, label: 'No' },
    ],
    close_time: '2099-01-05T12:00:00Z',
    resolution_time: '2099-01-10T12:00:00Z',
    resolution_criteria: 'Resolves YES if it rains.',
    resolution_sources: [],
    liquidity_b: '100.0000',
    seed_subsidy: '250.0000',
    published_at: '2026-01-01T00:00:00Z',
    proposed_outcome_id: null,
    ...overrides,
  }
}

function mockBalance(userId: string, body: Record<string, unknown>) {
  server.use(http.get(`${LEDGER_BASE}/ledger/users/${userId}/balance`, () => HttpResponse.json(body)))
}

function mockEntries(userId: string, entries: Record<string, unknown>[], nextCursor: string | null = null) {
  server.use(
    http.get(`${LEDGER_BASE}/ledger/users/${userId}/entries`, () =>
      HttpResponse.json({ entries, next_cursor: nextCursor, has_more: nextCursor !== null }),
    ),
  )
}

function mockMarket(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${MARKET_BASE}/public/markets/${id}`, () => HttpResponse.json(body)))
}

const AUTH_BASE = 'http://localhost:8000'

function suspensionResponse(overrides: Record<string, unknown> = {}) {
  return {
    user: { id: 'trader-1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', is_suspended: true, created_at: '2026-01-01' },
    takes_effect_within_seconds: 900,
    ...overrides,
  }
}

function mockSuspension(userId: string, handler: () => Response | Promise<Response>) {
  server.use(http.patch(`${AUTH_BASE}/admin/users/${userId}/suspension`, handler))
}

function renderPage(userId = 'trader-1', state?: Record<string, unknown>) {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={[{ pathname: `/admin/users/${userId}`, state }]}>
        <Routes>
          <Route path="/admin/users/:userId" element={<AdminUserHistoryPage />} />
          <Route path="/admin/users" element={<p>users list</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

// A router the test can move between two :userId values at the same route,
// so the page stays mounted and only the param changes — the shape a click
// from one user's history to another's would take if the list ever linked
// them directly (MarketDetailPage tests the same shape for its own :id).
function renderMovableRouter(userId: string) {
  const router = createMemoryRouter(
    [{ path: '/admin/users/:userId', element: <AdminUserHistoryPage /> }],
    { initialEntries: [`/admin/users/${userId}`] },
  )
  render(
    <WithProviders user={admin}>
      <RouterProvider router={router} />
    </WithProviders>,
  )
  return router
}

describe('AdminUserHistoryPage', () => {
  it('names whose history this is when linked from the user list', async () => {
    mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
    mockEntries('trader-1', [])
    renderPage('trader-1', { user: { id: 'trader-1', username: 'alice', email: 'alice@smu.edu.sg' } })

    expect(await screen.findByRole('heading', { name: /alice/i })).toBeInTheDocument()
    expect(screen.getByText('alice@smu.edu.sg')).toBeInTheDocument()
  })

  it('falls back to the user id when there is no linked user, as after a page reload', async () => {
    mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
    mockEntries('trader-1', [])
    renderPage('trader-1')

    expect(await screen.findByRole('heading', { name: /trader-1/i })).toBeInTheDocument()
  })

  it('shows the balance and the full ledger, newest first', async () => {
    mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
    mockEntries('trader-1', [entry()])
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('997.4687 credits')).toBeInTheDocument()
    expect(screen.getByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.getByText('-2.5313')).toBeInTheDocument()
  })

  it('shows a zero balance and an empty history for a user who has never been granted, not an error', async () => {
    mockBalance('trader-2', { user_id: 'trader-2', account_id: null, balance: '0.0000' })
    mockEntries('trader-2', [])
    renderPage('trader-2')

    expect(await screen.findByText('0.0000 credits')).toBeInTheDocument()
    expect(screen.getByText(/no activity for this user yet/i)).toBeInTheDocument()
  })

  it('shows an error when the history fails to load', async () => {
    mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
    server.use(http.get(`${LEDGER_BASE}/ledger/users/trader-1/entries`, () => HttpResponse.error()))
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load history/i)
  })

  it('shows an error when the balance fails to load, independent of the history', async () => {
    server.use(http.get(`${LEDGER_BASE}/ledger/users/trader-1/balance`, () => HttpResponse.error()))
    mockEntries('trader-1', [])
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load balance/i)
    expect(await screen.findByText(/no activity for this user yet/i)).toBeInTheDocument()
  })

  it('does not keep showing the previous user\'s balance while the next one\'s is still loading', async () => {
    mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '111.1111' })
    mockEntries('trader-1', [])
    const router = renderMovableRouter('trader-1')
    await screen.findByText('111.1111 credits')

    const trader2 = holdUntilReleased()
    server.use(
      http.get(`${LEDGER_BASE}/ledger/users/trader-2/balance`, async () => {
        await trader2.held
        return HttpResponse.json({ user_id: 'trader-2', account_id: 'acc-2', balance: '222.2222' })
      }),
    )
    mockEntries('trader-2', [])
    await act(() => router.navigate('/admin/users/trader-2'))

    // trader-2's balance has not answered yet: trader-1's stale number must
    // not still be on screen while it is pending.
    await screen.findByText(/no activity for this user yet/i)
    expect(screen.queryByText('111.1111 credits')).not.toBeInTheDocument()

    trader2.release()
    expect(await screen.findByText('222.2222 credits')).toBeInTheDocument()
  })

  it('shows a link back to the user list', async () => {
    mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
    mockEntries('trader-1', [])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText(/no activity for this user yet/i)

    await actor.click(screen.getByRole('link', { name: '← Users' }))

    expect(screen.getByText('users list')).toBeInTheDocument()
  })

  describe('suspending and unsuspending', () => {
    it('offers neither Suspend nor Unsuspend on the signed-in admin\'s own page', async () => {
      // auth-service.md: "do not offer the control on the signed-in user's
      // own row" for cannot_suspend_self, the same way as cannot_change_own_role.
      mockBalance(admin.id, { user_id: admin.id, account_id: 'acc-1', balance: '997.4687' })
      mockEntries(admin.id, [])
      renderPage(admin.id)

      await screen.findByRole('heading', { name: new RegExp(admin.id, 'i') })
      expect(screen.queryByRole('button', { name: /^suspend$/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /^unsuspend$/i })).not.toBeInTheDocument()
    })

    it('offers both Suspend and Unsuspend regardless of the account\'s current state', async () => {
      // [4.2] #14: suspended is the state to end up in, not a toggle, and
      // there is no GET /admin/users/{id} to know current state after a
      // reload — the control must work without ever having read it.
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      renderPage('trader-1')

      await screen.findByRole('heading', { name: /trader-1/i })
      expect(screen.getByRole('button', { name: /^suspend$/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^unsuspend$/i })).toBeInTheDocument()
    })

    it('requires a reason before Confirm Suspend is enabled', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      const actor = userEvent.setup()
      renderPage('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))

      const confirm = screen.getByRole('button', { name: /confirm suspend/i })
      expect(confirm).toBeDisabled()

      await actor.type(screen.getByLabelText(/reason/i), 'Second account of the same person')
      expect(confirm).toBeEnabled()
    })

    it('enables Confirm Suspend at exactly 10 trimmed characters, not 9', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      const actor = userEvent.setup()
      renderPage('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      const confirm = screen.getByRole('button', { name: /confirm suspend/i })
      const field = screen.getByLabelText(/reason/i)

      await actor.type(field, '  123456789  ')
      expect(confirm).toBeDisabled()

      await actor.type(field, '0')
      expect(confirm).toBeEnabled()
    })

    it('suspends the account and repaints the badge and balance page from the response, without a refetch', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      mockSuspension('trader-1', () => HttpResponse.json(suspensionResponse()))
      const actor = userEvent.setup()
      renderPage('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      await actor.type(screen.getByLabelText(/reason/i), 'Second account of the same person')
      await actor.click(screen.getByRole('button', { name: /confirm suspend/i }))

      expect(await screen.findByText('Suspended')).toBeInTheDocument()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      // The response names alice, not the id — the page now shows the name.
      expect(screen.getByRole('heading', { name: /alice/i })).toBeInTheDocument()
      // auth-service.md: "never zero, and the screen should say so" — the
      // suspension takes effect at once, but a token already issued is
      // honoured elsewhere for up to takes_effect_within_seconds.
      expect(screen.getByText(/up to 15 minutes/i)).toBeInTheDocument()
    })

    it('unsuspends the account and drops the Suspended badge from the response', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      mockSuspension('trader-1', () => HttpResponse.json(suspensionResponse({
        user: { id: 'trader-1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', is_suspended: false, created_at: '2026-01-01' },
      })))
      const actor = userEvent.setup()
      renderPage('trader-1', { user: { id: 'trader-1', username: 'alice', email: 'alice@smu.edu.sg', is_suspended: true } })
      await screen.findByText('Suspended')

      await actor.click(screen.getByRole('button', { name: /^unsuspend$/i }))
      await actor.type(screen.getByLabelText(/reason/i), 'Appeal reviewed and accepted')
      await actor.click(screen.getByRole('button', { name: /confirm unsuspend/i }))

      await screen.findByRole('heading', { name: /alice/i })
      expect(screen.queryByText('Suspended')).not.toBeInTheDocument()
      // Suspending ended every session and unsuspending brings none back
      // (auth-service.md), so there is nothing left that keeps working for
      // fifteen minutes; the user just logs in again.
      expect(screen.getByText(/they can log in again now/i)).toBeInTheDocument()
      expect(screen.queryByText(/15 minutes/i)).not.toBeInTheDocument()
    })

    it('shows a field-level error on a reason the server rejects, the same shape as close\'s, and keeps the modal open', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      mockSuspension('trader-1', () => HttpResponse.json(
        { error: { code: 'suspension_incomplete', message: 'bad reason', details: [{ field: 'reason', message: 'The reason must be at least 10 characters.' }] } },
        { status: 422 },
      ))
      const actor = userEvent.setup()
      renderPage('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      await actor.type(screen.getByLabelText(/reason/i), '1234567890')
      await actor.click(screen.getByRole('button', { name: /confirm suspend/i }))

      expect(await screen.findByText(/at least 10 characters/i)).toBeInTheDocument()
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    it('shows the cannot_suspend_self message and leaves the account unchanged', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      mockSuspension('trader-1', () => HttpResponse.json(
        { error: { code: 'cannot_suspend_self', message: 'no' } }, { status: 403 },
      ))
      const actor = userEvent.setup()
      renderPage('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      await actor.type(screen.getByLabelText(/reason/i), 'Testing my own account')
      await actor.click(screen.getByRole('button', { name: /confirm suspend/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent(/cannot suspend your own account/i)
      expect(screen.queryByText('Suspended')).not.toBeInTheDocument()
      // The popup is a full-screen overlay; a message left behind it would
      // be as good as not shown at all.
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('shows the last_administrator message and lets the admin retry rather than disabling the control', async () => {
      // Unlike a superseded Close or Settle, this is not a stale view: the
      // fix is to promote someone else first, and a retry after that could
      // succeed, so nothing here should be permanently disabled.
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      mockSuspension('trader-1', () => HttpResponse.json(
        { error: { code: 'last_administrator', message: 'no' } }, { status: 409 },
      ))
      const actor = userEvent.setup()
      renderPage('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      await actor.type(screen.getByLabelText(/reason/i), 'Last admin standing')
      await actor.click(screen.getByRole('button', { name: /confirm suspend/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent(/no administrator who can log in/i)
      expect(screen.getByRole('button', { name: /^suspend$/i })).toBeEnabled()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('cancels without changing the account', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      const actor = userEvent.setup()
      renderPage('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      await actor.click(screen.getByRole('button', { name: /cancel/i }))

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.queryByText('Suspended')).not.toBeInTheDocument()
    })

    it('drops a stale suspension error when navigating to a different user', async () => {
      // Same family of bug as the balance test above: the page is not
      // remounted when :userId changes, so state tied to "the last account
      // acted on" has to be cleared by hand rather than by a fresh mount.
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      mockSuspension('trader-1', () => HttpResponse.json(
        { error: { code: 'cannot_suspend_self', message: 'no' } }, { status: 403 },
      ))
      mockBalance('trader-2', { user_id: 'trader-2', account_id: 'acc-2', balance: '500.0000' })
      mockEntries('trader-2', [])
      const actor = userEvent.setup()
      const router = renderMovableRouter('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      await actor.type(screen.getByLabelText(/reason/i), 'Testing my own account')
      await actor.click(screen.getByRole('button', { name: /confirm suspend/i }))
      await screen.findByRole('alert')

      await act(() => router.navigate('/admin/users/trader-2'))

      await screen.findByRole('heading', { name: /trader-2/i })
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('drops the changed user\'s state when navigating to a different user', async () => {
      mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
      mockEntries('trader-1', [])
      mockSuspension('trader-1', () => HttpResponse.json(suspensionResponse()))
      mockBalance('trader-2', { user_id: 'trader-2', account_id: 'acc-2', balance: '500.0000' })
      mockEntries('trader-2', [])
      const actor = userEvent.setup()
      const router = renderMovableRouter('trader-1')
      await screen.findByRole('heading', { name: /trader-1/i })

      await actor.click(screen.getByRole('button', { name: /^suspend$/i }))
      await actor.type(screen.getByLabelText(/reason/i), 'Second account of the same person')
      await actor.click(screen.getByRole('button', { name: /confirm suspend/i }))
      await screen.findByText('Suspended')

      await act(() => router.navigate('/admin/users/trader-2'))

      await screen.findByRole('heading', { name: /trader-2/i })
      expect(screen.queryByText('Suspended')).not.toBeInTheDocument()
    })
  })

  it('appends the next page when Load more is clicked', async () => {
    mockBalance('trader-1', { user_id: 'trader-1', account_id: 'acc-1', balance: '997.4687' })
    let cursorsSeen: (string | null)[] = []
    server.use(
      http.get(`${LEDGER_BASE}/ledger/users/trader-1/entries`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        cursorsSeen.push(cursor)
        return cursor === 'page-2'
          ? HttpResponse.json({ entries: [entry({ id: 'e2', kind: 'signup_grant', amount: '1000.0000', balance_after: '1000.0000', market_id: null, outcome_id: null, side: null, quantity: null, average_price: null })], next_cursor: null, has_more: false })
          : HttpResponse.json({ entries: [entry()], next_cursor: 'page-2', has_more: true })
      }),
    )
    mockMarket('mkt-1', market())
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain in Singapore tomorrow?')
    expect(screen.queryByText('Starting grant')).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Starting grant')).toBeInTheDocument()
    expect(cursorsSeen).toEqual([null, 'page-2'])
  })
})
