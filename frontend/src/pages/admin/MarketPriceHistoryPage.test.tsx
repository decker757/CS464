import { render, screen, waitFor } from '@testing-library/react'
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
const AUTH_BASE = 'http://localhost:8000'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

const ALICE_ID = 'aaaaaaaa-1111-2222-3333-444444444444'
const BOB_ID = 'bbbbbbbb-1111-2222-3333-444444444444'

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
    user_id: ALICE_ID,
    outcome_id: 'o-yes',
    side: 'buy',
    quantity: '10.0000',
    average_price: '0.6000',
    total: '6.0000',
    state_version: 1,
    ...overrides,
  }
}

function pricePoint(occurredAt: string, stateVersion: number) {
  return {
    state_version: stateVersion,
    occurred_at: occurredAt,
    prices: [
      { outcome_id: 'o-yes', position: 0, price: '0.6000' },
      { outcome_id: 'o-no', position: 1, price: '0.4000' },
    ],
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

/**
 * Names the ids the page asks about, leaving out any id `usernames` has no
 * entry for, the way auth leaves an id that names no user out of the answer.
 * Gives back the batches it was asked for, so a test can show the page asked
 * once for the whole page of trades rather than once per row.
 */
function mockLookup(usernames: Record<string, string>): string[][] {
  const batches: string[][] = []
  server.use(http.post(`${AUTH_BASE}/admin/users/lookup`, async ({ request }) => {
    const { user_ids } = (await request.json()) as { user_ids: string[] }
    batches.push(user_ids)
    const named = user_ids.filter(id => id in usernames).map(id => ({ id, username: usernames[id] }))
    return HttpResponse.json({ users: named })
  }))
  return batches
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
    expect(screen.getByText(/showing the only price point/i)).toBeInTheDocument()
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

  it('names the winning outcome in the settlement note, with no price history for a market with no trades', async () => {
    mockMarket('mkt-1', market({ status: 'approved' }))
    mockPriceHistory('mkt-1', [], { result: 'settled', winning_outcome_id: 'o-yes', result_recorded_at: '2026-02-01T00:00:00Z' })
    mockTrades('mkt-1', [])

    renderPage()

    expect(await screen.findByText(/^this market settled on .*; Yes won\.$/i)).toBeInTheDocument()
    expect(screen.getByText(/no price history yet/i)).toBeInTheDocument()
  })

  it('says when the screen was read, and reads the chart and the trade log again on Refresh', async () => {
    // Prices move with every trade, so an admin who left the tab open through
    // a burst must be able to see that and ask again (#361).
    mockMarket('mkt-1', market())
    let historyReads = 0
    server.use(http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/price-history`, () => {
      historyReads += 1
      const points = [pricePoint('2026-01-01T00:00:00Z', 0)]
      if (historyReads > 1) points.push(pricePoint('2026-01-01T01:00:00Z', 1))
      return HttpResponse.json({ market_id: 'mkt-1', points, result: null, winning_outcome_id: null, result_recorded_at: null })
    }))
    let tradeReads = 0
    server.use(http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/trades`, () => {
      tradeReads += 1
      const trades = [trade()]
      if (tradeReads > 1) trades.push(trade({ transaction_id: 't2', user_id: BOB_ID }))
      return HttpResponse.json({ trades, next_cursor: null, has_more: false })
    }))
    const actor = userEvent.setup()
    renderPage()
    expect(await screen.findByText(/showing the only price point/i)).toBeInTheDocument()
    expect(screen.getByText(/^read at /i)).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: 'Refresh' }))

    expect(await screen.findByText(/showing all 2 price points/i)).toBeInTheDocument()
    expect(await screen.findByText('bbbbbbbb')).toBeInTheDocument()
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
          ? HttpResponse.json({ trades: [trade({ transaction_id: 't2', user_id: BOB_ID })], next_cursor: null, has_more: false })
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

  it('names every trader on the trade log, asking auth once for the whole page', async () => {
    // [2.4] #434. The ledger sends a user_id and no username, because it
    // holds no user table (ADR 0003), so the browser joins the names in
    // through auth's batch lookup — one request for the page, not one a row.
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [])
    mockTrades('mkt-1', [trade(), trade({ transaction_id: 't2', user_id: BOB_ID })])
    const batches = mockLookup({ [ALICE_ID]: 'alice', [BOB_ID]: 'bob' })

    renderPage()

    expect(await screen.findByRole('link', { name: 'alice' })).toHaveAttribute('href', `/admin/users/${ALICE_ID}`)
    expect(screen.getByRole('link', { name: 'bob' })).toHaveAttribute('href', `/admin/users/${BOB_ID}`)
    expect(batches).toEqual([[ALICE_ID, BOB_ID]])
  })

  it('leaves a trader auth did not name on their short id', async () => {
    // An id that names no user is left out of the answer rather than refused
    // (auth-service.md), and that row shows what it showed before the names
    // arrived — never a blank cell.
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [])
    mockTrades('mkt-1', [trade(), trade({ transaction_id: 't2', user_id: BOB_ID })])
    mockLookup({ [ALICE_ID]: 'alice' })

    renderPage()

    expect(await screen.findByRole('link', { name: 'alice' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'bbbbbbbb' })).toBeInTheDocument()
  })

  it('keeps the trade log when the lookup fails, with no error of its own', async () => {
    // The names sit on top of rows that are already on screen, so a failed
    // lookup degrades to the short ids it replaced and must never empty the
    // table or report a failure the admin can do nothing about (#434).
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [{ state_version: 0, occurred_at: '2026-01-01T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.6000' }] }])
    mockTrades('mkt-1', [trade()])
    let timesAsked = 0
    server.use(http.post(`${AUTH_BASE}/admin/users/lookup`, () => {
      timesAsked += 1
      return HttpResponse.error()
    }))

    renderPage()

    // Asserted after the lookup has failed, not merely before it answered:
    // the short id is on screen while the request is still in flight, so
    // waiting for the failure is what makes this about the failure.
    await screen.findByRole('link', { name: 'aaaaaaaa' })
    await waitFor(() => expect(timesAsked).toBe(1))
    expect(screen.getByRole('link', { name: 'aaaaaaaa' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('asks only about the ids the next page of trades adds', async () => {
    // One batch per page of trades, so a log paged well past auth's limit of
    // 200 ids a request can never ask for more than it will name
    // (useUsernames, auth-service.md).
    mockMarket('mkt-1', market())
    mockPriceHistory('mkt-1', [])
    server.use(
      http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/trades`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        return cursor === 'page-2'
          ? HttpResponse.json({ trades: [trade({ transaction_id: 't2', user_id: BOB_ID })], next_cursor: null, has_more: false })
          : HttpResponse.json({ trades: [trade()], next_cursor: 'page-2', has_more: true })
      }),
    )
    const batches = mockLookup({ [ALICE_ID]: 'alice', [BOB_ID]: 'bob' })
    const actor = userEvent.setup()
    renderPage()
    await screen.findByRole('link', { name: 'alice' })

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByRole('link', { name: 'bob' })).toBeInTheDocument()
    expect(batches).toEqual([[ALICE_ID], [BOB_ID]])
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
