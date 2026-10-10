import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
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

function renderPage(userId = 'trader-1') {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={[`/admin/users/${userId}`]}>
        <Routes>
          <Route path="/admin/users/:userId" element={<AdminUserHistoryPage />} />
          <Route path="/admin/users" element={<p>users list</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('AdminUserHistoryPage', () => {
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

    expect(await screen.findByRole('alert', { name: '' })).toBeInTheDocument()
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
