import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import MarketPriceHistoryPage from './MarketPriceHistoryPage'

const LEDGER_BASE = 'http://localhost:8003'
const MARKET_BASE = 'http://localhost:8001'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

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

function trade(overrides: Record<string, unknown> = {}) {
  return {
    transaction_id: 't1',
    created_at: '2026-01-02T12:00:00Z',
    user_id: 'aaaaaaaa-1111-2222-3333-444444444444',
    outcome_id: 'o-yes',
    side: 'buy',
    quantity: '10.0000',
    average_price: '0.6000',
    total: '6.0000',
    state_version: 1,
    ...overrides,
  }
}

function mockMarket(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${MARKET_BASE}/public/markets/${id}`, () => HttpResponse.json(body)))
}

function mockPriceHistory(id: string, points: Record<string, unknown>[], overrides: Record<string, unknown> = {}) {
  server.use(http.get(`${LEDGER_BASE}/ledger/markets/${id}/price-history`, () => HttpResponse.json({
    market_id: id, points, result: null, winning_outcome_id: null, result_recorded_at: null, ...overrides,
  })))
}

function mockTrades(id: string, trades: Record<string, unknown>[], nextCursor: string | null = null) {
  server.use(http.get(`${LEDGER_BASE}/ledger/markets/${id}/trades`, () => HttpResponse.json({ trades, next_cursor: nextCursor, has_more: nextCursor !== null })))
}

function renderPage(marketId = 'mkt-1') {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={[`/admin/markets/${marketId}/price-history`]}>
        <Routes>
          <Route path="/admin/markets/:id/price-history" element={<MarketPriceHistoryPage />} />
          <Route path="/admin/markets" element={<p>all markets</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('MarketPriceHistoryPage', () => {
  it('shows the market question, the price chart and the trade log', async () => {
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [
      { state_version: 0, occurred_at: '2026-01-01T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.6000' }, { outcome_id: 'o-no', position: 1, price: '0.4000' }] },
    ])
    mockTrades('mkt-1', [trade()])

    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(await screen.findByText('aaaaaaaa')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByText(/no price history yet/i)).not.toBeInTheDocument()
  })

  it('shows an empty state when the market has no price history yet', async () => {
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [])
    mockTrades('mkt-1', [])

    renderPage()

    expect(await screen.findByText(/no price history yet/i)).toBeInTheDocument()
    expect(await screen.findByText(/no trades on this market yet/i)).toBeInTheDocument()
  })

  it('shows an error when the market fails to load', async () => {
    server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => HttpResponse.error()))
    mockPriceHistory('mkt-1', [])
    mockTrades('mkt-1', [])

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load this market/i)
  })

  it('shows an error on the chart when price history fails, independent of the trade log', async () => {
    mockMarket('mkt-1', market())
    server.use(http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/price-history`, () => HttpResponse.error()))
    mockTrades('mkt-1', [trade()])

    renderPage()

    expect(await screen.findByText(/failed to load price history/i)).toBeInTheDocument()
    expect(await screen.findByText('aaaaaaaa')).toBeInTheDocument()
  })

  it('shows an error on the trade log when it fails, independent of the chart', async () => {
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [
      { state_version: 0, occurred_at: '2026-01-01T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.6000' }] },
    ])
    server.use(http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/trades`, () => HttpResponse.error()))

    renderPage()

    expect(await screen.findByText(/failed to load trades/i)).toBeInTheDocument()
    expect(screen.queryByText(/no price history yet/i)).not.toBeInTheDocument()
  })

  it('shows the settlement note and no price history yet for a market with no trades, after it has settled', async () => {
    mockMarket('mkt-1', market({ status: 'approved' }))
    mockPriceHistory('mkt-1', [], { result: 'settled', winning_outcome_id: 'o-yes', result_recorded_at: '2026-02-01T00:00:00Z' })
    mockTrades('mkt-1', [])

    renderPage()

    expect(await screen.findByText(/this market settled/i)).toBeInTheDocument()
    expect(screen.getByText(/no price history yet/i)).toBeInTheDocument()
  })

  it('appends the next page of trades when Load more is clicked', async () => {
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [])
    let cursorsSeen: (string | null)[] = []
    server.use(
      http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/trades`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        cursorsSeen.push(cursor)
        return cursor === 'page-2'
          ? HttpResponse.json({ trades: [trade({ transaction_id: 't2', user_id: 'bbbbbbbb-1111-2222-3333-444444444444' })], next_cursor: null, has_more: false })
          : HttpResponse.json({ trades: [trade()], next_cursor: 'page-2', has_more: true })
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('aaaaaaaa')

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('bbbbbbbb')).toBeInTheDocument()
    expect(cursorsSeen).toEqual([null, 'page-2'])
  })

  it('shows a link back to all markets', async () => {
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [])
    mockTrades('mkt-1', [])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain in Singapore tomorrow?')

    await actor.click(screen.getByRole('link', { name: '← All Markets' }))

    expect(screen.getByText('all markets')).toBeInTheDocument()
  })
})
