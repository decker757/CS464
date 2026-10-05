import { StrictMode } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../context/AuthContext'
import { WithProviders } from '../test/renderWithProviders'
import { server } from '../test/server'
import TradeHistoryPage from './TradeHistoryPage'

const LEDGER_BASE = 'http://localhost:8003'
const MARKET_BASE = 'http://localhost:8001'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

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

function grantEntry(overrides: Record<string, unknown> = {}) {
  return {
    id: 'e0',
    created_at: '2026-09-15T04:21:09.412883Z',
    amount: '1000.0000',
    balance_after: '1000.0000',
    transaction_id: 't0',
    kind: 'signup_grant',
    context: { user_id: '1' },
    market_id: null,
    outcome_id: null,
    side: null,
    quantity: null,
    average_price: null,
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

function mockEntries(entries: Record<string, unknown>[], nextCursor: string | null = null) {
  server.use(
    http.get(`${LEDGER_BASE}/ledger/entries/me`, () =>
      HttpResponse.json({ entries, next_cursor: nextCursor, has_more: nextCursor !== null }),
    ),
  )
}

function mockMarket(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${MARKET_BASE}/public/markets/${id}`, () => HttpResponse.json(body)))
}

function renderPage() {
  return render(
    <StrictMode>
      <WithProviders user={trader}>
        <MemoryRouter initialEntries={['/history']}>
          <Routes>
            <Route path="/history" element={<TradeHistoryPage />} />
            <Route path="/markets/:id" element={<p>market detail</p>} />
          </Routes>
        </MemoryRouter>
      </WithProviders>
    </StrictMode>,
  )
}

// A cursor of 'page-2' carries page one's request to page two, same as #104's markets pattern.
function mockTwoPages(secondPage: () => Response | Promise<Response>) {
  server.use(
    http.get(`${LEDGER_BASE}/ledger/entries/me`, ({ request }) =>
      new URL(request.url).searchParams.get('cursor') === 'page-2'
        ? secondPage()
        : HttpResponse.json({ entries: [entry()], next_cursor: 'page-2', has_more: true }),
    ),
  )
}

describe('TradeHistoryPage', () => {
  it('shows a trade row with its market question, outcome, quantity, average price, amount and balance after', async () => {
    mockEntries([entry()])
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.getByText('Buy Yes')).toBeInTheDocument()
    expect(screen.getByText('5.0000')).toBeInTheDocument()
    // average_price is the average fill price (ledger-service.md), shown as
    // a percentage like every other price on screen.
    expect(screen.getByText('50.6%')).toBeInTheDocument()
    expect(screen.getByText('-2.5313')).toBeInTheDocument()
    expect(screen.getByText('997.4687')).toBeInTheDocument()
  })

  it('shows the starting grant as a generic row, with no market link and a dash for quantity and average price', async () => {
    mockEntries([grantEntry()])
    renderPage()

    expect(await screen.findByText('Starting grant')).toBeInTheDocument()
    // amount and balance_after are both 1,000.0000 for a brand-new grant.
    expect(screen.getAllByText('1,000.0000')).toHaveLength(2)
    // quantity and average_price are both null on a non-trade row.
    expect(screen.getAllByText('—')).toHaveLength(2)
    expect(within(screen.getByRole('table')).queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders an unrecognised kind verbatim rather than failing', async () => {
    mockEntries([entry({ kind: 'settlement', market_id: null, outcome_id: null, side: null, quantity: null, average_price: null })])
    renderPage()

    expect(await screen.findByText('settlement')).toBeInTheDocument()
  })

  it('shows rows newest first, as the server sends them', async () => {
    mockEntries([entry({ id: 'e2', market_id: null, outcome_id: null, quantity: null, average_price: null, kind: 'settlement' }), grantEntry()])
    renderPage()

    await screen.findByText('settlement')
    const rows = screen.getAllByRole('row').slice(1) // drop the header row
    expect(rows[0]).toHaveTextContent('settlement')
    expect(rows[1]).toHaveTextContent('Starting grant')
  })

  it('shows an empty state when there is no history', async () => {
    mockEntries([])
    renderPage()
    expect(await screen.findByText(/no activity yet/i)).toBeInTheDocument()
  })

  it('shows an error when the API fails', async () => {
    server.use(http.get(`${LEDGER_BASE}/ledger/entries/me`, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('still shows the row when its market fails to load', async () => {
    mockEntries([entry()])
    server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => HttpResponse.error()))
    renderPage()

    expect(await screen.findByText('Unknown market')).toBeInTheDocument()
    expect(screen.getByText('997.4687')).toBeInTheDocument()
  })

  it('appends the next page when Load more is clicked', async () => {
    mockTwoPages(() => HttpResponse.json({ entries: [grantEntry()], next_cursor: null, has_more: false }))
    mockMarket('mkt-1', market())
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain in Singapore tomorrow?')
    expect(screen.queryByText('Starting grant')).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Starting grant')).toBeInTheDocument()
    expect(screen.getByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  it('keeps the rows already shown when loading more fails, and loads them on a retry', async () => {
    let secondPageRequests = 0
    mockTwoPages(() => {
      secondPageRequests += 1
      return secondPageRequests === 1
        ? HttpResponse.error()
        : HttpResponse.json({ entries: [grantEntry()], next_cursor: null, has_more: false })
    })
    mockMarket('mkt-1', market())
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain in Singapore tomorrow?')

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load more history/i)
    expect(screen.getByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Starting grant')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
