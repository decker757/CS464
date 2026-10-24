import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse, ws } from 'msw'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../context/AuthContext'
import { holdUntilReleased } from '../test/holdUntilReleased'
import { server } from '../test/server'
import { WithProviders } from '../test/renderWithProviders'
import MarketDetailPage from './MarketDetailPage'

const MARKET_BASE = 'http://localhost:8001'
const LEDGER_BASE = 'http://localhost:8003'
const SNAPSHOT = `${LEDGER_BASE}/ledger/markets/:id/snapshot`
const PORTFOLIO = `${LEDGER_BASE}/ledger/portfolio/me`
const ORDERS = `${LEDGER_BASE}/ledger/orders/me`
const PREVIEW = `${LEDGER_BASE}/ledger/markets/:id/preview`
const TRADES = `${LEDGER_BASE}/ledger/markets/:id/trades`

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

const baseMarket = {
  id: 'mkt-abc',
  status: 'open' as const,
  question: 'Will Singapore core inflation be below 2% for December 2026?',
  description: 'Measured on the first published print.',
  outcomes: [
    { id: 'o1', position: 0, label: 'Yes' },
    { id: 'o2', position: 1, label: 'No' },
  ],
  close_time: '2099-01-05T12:00:00Z',
  resolution_time: '2099-01-20T12:00:00Z',
  resolution_criteria: 'Resolves YES if the MAS core inflation print is strictly below 2.0%.',
  resolution_sources: [
    { id: 's1', position: 0, url: 'https://www.mas.gov.sg/statistics', label: 'MAS statistics' },
  ],
  liquidity_b: '100.0000',
  seed_subsidy: '250.0000',
  published_at: '2026-09-13T14:12:03Z',
  proposed_outcome_id: null,
}

// The trader's own positions, which the page reads for the sell clamp
// (#357). `position` carries only the three fields the page joins on.
function portfolio(positions: { market_id: string; outcome_id: string; quantity: string }[] = []) {
  return {
    user_id: '1',
    account_id: 'acct-1',
    balance: '1000.0000',
    positions_value: '0.0000',
    net_worth: '1000.0000',
    positions: positions.map(p => ({ ...p, outcome_position: 0, cost_basis: '0.0000', average_entry_price: '0.5000', price: '0.5000', value: '0.0000', unrealized_pnl: '0.0000', result: null, payout: null, state_version: 1 })),
  }
}

// A quote and a filled trade for the one test that buys through the panel, to
// reach the holding read that follows a trade. The panel's own behaviour is
// TradingPanel.test.tsx's job.
function tradeQuote() {
  return {
    market_id: 'mkt-abc',
    state_version: 1,
    side: 'buy',
    outcome_id: 'o1',
    quantity: '10.0000',
    total: '-5.1250',
    average_price: '0.5125',
    prices: [{ outcome_id: 'o1', position: 0, price: '0.5000' }, { outcome_id: 'o2', position: 1, price: '0.5000' }],
    post_trade_prices: [{ outcome_id: 'o1', position: 0, price: '0.5250' }, { outcome_id: 'o2', position: 1, price: '0.4750' }],
  }
}

function tradeResult() {
  return {
    transaction_id: 'tx-1',
    user_id: '1',
    market_id: 'mkt-abc',
    outcome_id: 'o1',
    side: 'buy',
    quantity: '10.0000',
    total: '-5.1250',
    state_version: 2,
  }
}

// MSW WebSocket link for the realtime price socket. Silently accepts
// connections so the hook connects without errors; indicator tests drive
// the close from the server side.
const priceService = ws.link('ws://localhost:8004/ws/prices')

// Stub WebSocket — most tests don't exercise live prices (that's in
// useMarketPrices tests). A silent MSW handler keeps the hook happy. The
// portfolio read is here rather than in one describe's own beforeEach because
// every test that renders a tradeable market makes it, this one included.
beforeEach(() => {
  server.use(
    priceService.addEventListener('connection', () => { /* silent */ }),
    http.get(PORTFOLIO, () => HttpResponse.json(portfolio())),
    // Every binary market reads this for the open-orders list ([T-6] #61);
    // no orders is the default, so most tests see no list at all.
    http.get(ORDERS, () => HttpResponse.json({ orders: [] })),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage(marketId = 'mkt-abc', { refetchBalance }: { refetchBalance?: () => Promise<void> } = {}) {
  return render(
    <WithProviders user={trader} refetchBalance={refetchBalance}>
      <MemoryRouter initialEntries={[`/markets/${marketId}`]}>
        <Routes>
          <Route path="/markets/:id" element={<MarketDetailPage />} />
          <Route path="/markets" element={<p>markets list</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

// A router the test can move between markets, so the page stays mounted and
// only the :id changes, as it does when a link goes from one market to another.
function renderMovableRouter(marketId: string) {
  const router = createMemoryRouter(
    [{ path: '/markets/:id', element: <MarketDetailPage /> }],
    { initialEntries: [`/markets/${marketId}`] },
  )
  render(
    <WithProviders user={trader}>
      <RouterProvider router={router} />
    </WithProviders>,
  )
  return router
}

function priceFrame(version: number, yes: string, no: string) {
  return {
    market_id: 'mkt-abc',
    state_version: version,
    prices: [
      { outcome_id: 'o1', position: 0, price: yes },
      { outcome_id: 'o2', position: 1, price: no },
    ],
    occurred_at: '2026-09-26T00:00:00Z',
  }
}

describe('MarketDetailPage', () => {
  beforeEach(() => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json(baseMarket),
      ),
      // No price unless a test serves one, so the placeholder stays on screen.
      http.get(SNAPSHOT, () =>
        HttpResponse.json({ error: { code: 'market_not_found', message: 'Not found.' } }, { status: 404 }),
      ),
    )
  })

  it('shows the market question and description', async () => {
    renderPage()
    expect(await screen.findByText('Will Singapore core inflation be below 2% for December 2026?')).toBeInTheDocument()
    expect(screen.getByText('Measured on the first published print.')).toBeInTheDocument()
  })

  it('shows the status badge', async () => {
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2% for December 2026?')
    expect(screen.getByText('Open')).toBeInTheDocument()
  })

  it('shows a placeholder on each outcome until the first price arrives', async () => {
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2% for December 2026?')
    expect(screen.getByLabelText('Yes price')).toHaveTextContent('—')
    expect(screen.getByLabelText('No price')).toHaveTextContent('—')
    expect(screen.getAllByText('loading price…').length).toBe(2)
  })

  // AC 1 and AC 3: the page shows the ledger's snapshot, then what a trade pushes.
  it('shows the snapshot price, then the price a trade pushes', async () => {
    let wsClient: { send(data: string): void } | null = null
    server.use(
      http.get(SNAPSHOT, () => HttpResponse.json(priceFrame(5, '0.6000', '0.4000'))),
      priceService.addEventListener('connection', ({ client }) => {
        wsClient = client
        client.addEventListener('message', event => {
          const message = JSON.parse(event.data as string)
          if (message.action === 'subscribe') {
            client.send(JSON.stringify({ type: 'subscribed', market_id: message.market_id }))
          }
        })
      }),
    )
    renderPage()
    await waitFor(() => expect(screen.getByLabelText('Yes price')).toHaveTextContent('60.0%'))
    expect(screen.getByLabelText('No price')).toHaveTextContent('40.0%')
    expect(screen.queryByText('loading price…')).not.toBeInTheDocument()

    await waitFor(() => expect(wsClient).not.toBeNull())
    act(() => wsClient!.send(JSON.stringify({ type: 'price', ...priceFrame(6, '0.7000', '0.3000') })))
    await waitFor(() => expect(screen.getByLabelText('Yes price')).toHaveTextContent('70.0%'))
    expect(screen.getByLabelText('No price')).toHaveTextContent('30.0%')
  })

  it('says closed early, not a future closing date, for a market an admin closed', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status: 'closed' }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Closed early')).toBeInTheDocument()
    expect(screen.queryByText(/^Closes \d/)).not.toBeInTheDocument()
  })

  it('shows the trading panel for open markets', async () => {
    // The panel's own behaviour (preview, submit, error handling) is
    // TradingPanel.test.tsx's job; this just checks it is the thing shown.
    renderPage()
    expect(await screen.findByRole('button', { name: /^buy$/i })).toBeInTheDocument()
    expect(screen.getByLabelText('Quantity (shares)')).toBeInTheDocument()
  })

  // The panel's sell clamp needs the trader's position on *this* market and
  // outcome, and the portfolio route carries every market's (#357). The join
  // is this page's job, so only this test can catch it reading the wrong row.
  it('gives the trading panel the holding on this market and outcome', async () => {
    server.use(
      http.get(PORTFOLIO, () => HttpResponse.json(portfolio([
        { market_id: 'mkt-abc', outcome_id: 'o2', quantity: '6.0000' },
        // Another market, and an outcome id this market also has: a lookup
        // that forgets the market would read 99 shares of Yes here.
        { market_id: 'mkt-other', outcome_id: 'o1', quantity: '99.0000' },
      ]))),
    )
    const actor = userEvent.setup()
    renderPage()
    await actor.click(await screen.findByRole('button', { name: /^sell$/i }))

    expect(await screen.findByText(/you hold 0\.0000 shares/i)).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: 'No' }))

    expect(screen.getByText(/you hold 6\.0000 shares/i)).toBeInTheDocument()
  })

  it('keeps the holding read after a trade when the slower read from before it answers last', async () => {
    // The portfolio route values every position and mints the signup grant on
    // a first read, so the mount's read can easily answer after the one a
    // trade fires. Taking the last answer to arrive would leave the panel
    // refusing to sell a position the ledger holds, for the rest of the visit:
    // nothing re-fires the read, so only a reload would recover it.
    const { held, release } = holdUntilReleased()
    let reads = 0
    server.use(
      http.get(PORTFOLIO, async () => {
        reads += 1
        if (reads > 1) {
          return HttpResponse.json(portfolio([{ market_id: 'mkt-abc', outcome_id: 'o1', quantity: '10.0000' }]))
        }
        // The picture from before the buy, on its way out since mount.
        await held
        return HttpResponse.json(portfolio())
      }),
      http.get(PREVIEW, () => HttpResponse.json(tradeQuote())),
      http.post(TRADES, () => HttpResponse.json(tradeResult(), { status: 201 })),
    )
    const actor = userEvent.setup()
    renderPage()

    await actor.type(await screen.findByLabelText('Quantity (shares)'), '10')
    const buy = screen.getByRole('button', { name: /^buy yes$/i })
    await waitFor(() => expect(buy).toBeEnabled())
    await actor.click(buy)
    await screen.findByText(/bought/i)

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await waitFor(() => expect(screen.getByLabelText('shares held')).toHaveTextContent('You hold 10.0000 shares'))

    await act(async () => {
      release()
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(screen.getByLabelText('shares held')).toHaveTextContent('You hold 10.0000 shares')
  })

  it('shows the holding of the market it moved to', async () => {
    const otherMarket = {
      ...baseMarket,
      id: 'mkt-other',
      question: 'Will the other market resolve yes?',
      outcomes: [{ id: 'p1', position: 0, label: 'Yes' }, { id: 'p2', position: 1, label: 'No' }],
    }
    // The second market's read is held, so the gap after the move is visible:
    // outcome ids differ between markets, so the first market's answer would
    // read as "holds nothing" here and clamp every sell on that.
    const { held, release } = holdUntilReleased()
    let reads = 0
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, ({ params }) =>
        HttpResponse.json(params.id === 'mkt-other' ? otherMarket : baseMarket),
      ),
      http.get(PORTFOLIO, async () => {
        reads += 1
        if (reads > 1) await held
        return HttpResponse.json(portfolio([
          { market_id: 'mkt-abc', outcome_id: 'o1', quantity: '3.0000' },
          { market_id: 'mkt-other', outcome_id: 'p1', quantity: '7.0000' },
        ]))
      }),
    )
    const actor = userEvent.setup()
    const router = renderMovableRouter('mkt-abc')
    await actor.click(await screen.findByRole('button', { name: /^sell$/i }))
    await waitFor(() => expect(screen.getByLabelText('shares held')).toHaveTextContent('You hold 3.0000 shares'))

    await act(() => router.navigate('/markets/mkt-other'))

    await screen.findByText(otherMarket.question)
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    expect(screen.queryByLabelText('shares held')).not.toBeInTheDocument()

    release()

    await waitFor(() => expect(screen.getByLabelText('shares held')).toHaveTextContent('You hold 7.0000 shares'))
  })

  it('shows trading closed message for closed markets', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status: 'closed' }),
      ),
    )
    renderPage()
    expect(await screen.findByText(/trading is closed/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /buy/i })).not.toBeInTheDocument()
  })

  it('shows pending banner for pending_resolution markets', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status: 'pending_resolution' }),
      ),
    )
    renderPage()
    expect(await screen.findByText(/awaiting outcome decision/i)).toBeInTheDocument()
  })

  it.each([
    { status: 'pending_resolution', label: 'Proposed outcome', closed: 'Trading is closed while the outcome is decided.' },
    { status: 'approved', label: 'Approved outcome', closed: 'The outcome is approved and payouts are pending. Trading is closed.' },
    { status: 'settled', label: 'Settled', closed: 'This market has been settled. Trading is closed.' },
  ])('names the outcome on record for a $status market as "$label"', async ({ status, label, closed }) => {
    // Only a settled market is final, so only it may say "Settled", in the
    // banner or in why trading is closed.
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status, proposed_outcome_id: 'o1' }),
      ),
    )
    renderPage()
    const banner = await screen.findByText(`${label}:`, { exact: false })
    expect(banner.querySelector('strong')).toHaveTextContent('Yes')
    expect(screen.getByText(closed)).toBeInTheDocument()
  })

  // A status the backend adds before the frontend knows it (as #12's `settled`
  // once was); the page must still render rather than go blank on it.
  it('shows a market whose status the page does not know yet', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status: 'under_review' }),
      ),
    )
    renderPage()
    expect(await screen.findByText(baseMarket.question)).toBeInTheDocument()
    expect(screen.getByText('under review')).toBeInTheDocument()
  })

  it('shows resolution criteria and sources', async () => {
    renderPage()
    await screen.findByText('Resolves YES if the MAS core inflation print is strictly below 2.0%.')
    expect(screen.getByRole('link', { name: 'MAS statistics' })).toHaveAttribute('href', 'https://www.mas.gov.sg/statistics')
  })

  it('shows an error when the API fails', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.error()),
    )
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('navigates back to /markets via the back link', async () => {
    const { container } = renderPage()
    await screen.findByText('Will Singapore core inflation be below 2% for December 2026?')
    const backLink = container.querySelector('a[href="/markets"]')
    expect(backLink).toBeInTheDocument()
  })

  describe('when the route moves to another market', () => {
    const closedMarket = {
      ...baseMarket,
      id: 'mkt-closed',
      question: 'Did it rain on National Day 2025?',
      status: 'closed' as const,
      close_time: '2025-08-09T12:00:00Z',
    }

    it('shows the trading controls of an open market after a closed one', async () => {
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, ({ params }) =>
          HttpResponse.json(params.id === 'mkt-closed' ? closedMarket : baseMarket),
        ),
      )
      const router = renderMovableRouter('mkt-closed')
      expect(await screen.findByText(/trading is closed/i)).toBeInTheDocument()

      await act(() => router.navigate('/markets/mkt-abc'))

      expect(await screen.findByRole('button', { name: /buy yes/i })).toBeInTheDocument()
      expect(screen.queryByText(/trading is closed/i)).not.toBeInTheDocument()
    })

    it('drops the load error of a market that failed', async () => {
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, ({ params }) =>
          params.id === 'mkt-missing'
            ? HttpResponse.json({ error: { code: 'market_not_found', message: 'Not found.' } }, { status: 404 })
            : HttpResponse.json(baseMarket),
        ),
      )
      const router = renderMovableRouter('mkt-missing')
      expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)

      await act(() => router.navigate('/markets/mkt-abc'))

      expect(await screen.findByText(baseMarket.question)).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })
  })

  // [X-3] #36: the clock closes a market, not the status (ADR 0011), so the
  // page locks trading at close_time even while the status still says open.
  describe('at the close time', () => {
    afterEach(() => {
      vi.useRealTimers()
    })

    it('shows trading closed, and a closed badge, for an open market whose close time has passed', async () => {
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () =>
          HttpResponse.json({ ...baseMarket, close_time: '2025-08-09T12:00:00Z' }),
        ),
      )
      renderPage()
      expect(await screen.findByText(/trading is closed/i)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /buy/i })).not.toBeInTheDocument()
      // #357's fourth finding: this is the sweep window, where the status
      // column still reads `open` because CLOSED is written a few seconds
      // later (ADR 0011). A badge read straight off the column would say Open
      // directly above the line asserted above.
      expect(screen.getByText('Closed')).toBeInTheDocument()
      expect(screen.queryByText('Open')).not.toBeInTheDocument()
    })

    it('locks trading when the countdown reaches the close time', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      const closeTime = new Date(Date.now() + 5000).toISOString()
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () =>
          HttpResponse.json({ ...baseMarket, close_time: closeTime }),
        ),
      )
      renderPage()
      expect(await screen.findByRole('button', { name: /buy yes/i })).toBeInTheDocument()

      await act(() => vi.advanceTimersByTimeAsync(5000))

      expect(screen.getByText(/trading is closed/i)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /buy/i })).not.toBeInTheDocument()
    })
  })

  // [FE][3.3] #59: the countdown is display only (market-service.md's
  // dispute-screen notes, point 1) — shown on an approved market, nowhere else.
  describe('the dispute window countdown', () => {
    it('shows time left and the absolute instant for an approved market', async () => {
      const endsAt = new Date(Date.now() + 5 * 60 * 60 * 1000 + 12 * 60 * 1000).toISOString()
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () =>
          HttpResponse.json({ ...baseMarket, status: 'approved', proposed_outcome_id: 'o1', settleable: false, dispute_window_ends_at: endsAt }),
        ),
      )
      renderPage()
      expect(await screen.findByText(/disputes close in 5h 1[12]m/i)).toBeInTheDocument()
    })

    it('shows the result is final once the window has passed, with the closed instant so a wrong clock is visible', async () => {
      const endsAt = new Date(Date.now() - 1000).toISOString()
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () =>
          HttpResponse.json({ ...baseMarket, status: 'approved', proposed_outcome_id: 'o1', settleable: true, dispute_window_ends_at: endsAt }),
        ),
      )
      renderPage()
      expect(await screen.findByText(/result is final; payout pending \(closed .+\)\./i)).toBeInTheDocument()
    })

    it('shows no countdown for a market that is not approved, even with a live window', async () => {
      // Everything else is what an approved market has, so the status is the
      // only thing that can be hiding the countdown. With the default fixture
      // there is no window at all and the gate is never what does the work.
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () =>
          HttpResponse.json({
            ...baseMarket,
            status: 'closed',
            proposed_outcome_id: 'o1',
            dispute_window_ends_at: new Date(Date.now() + 300_000).toISOString(),
          }),
        ),
      )
      renderPage()
      await screen.findByText(baseMarket.question)
      expect(screen.queryByText(/disputes close in/i)).not.toBeInTheDocument()
      expect(screen.queryByText(/result is final/i)).not.toBeInTheDocument()
    })

    it('turns into "Result is final" on its own, with the page left open', async () => {
      // An approved market with no trades gets no price frame to force a
      // re-render, so without a timer this could sit on screen for hours
      // after the window actually closed.
      vi.useFakeTimers({ shouldAdvanceTime: true })
      try {
        const endsAt = new Date(Date.now() + 1000).toISOString()
        server.use(
          http.get(`${MARKET_BASE}/public/markets/:id`, () =>
            HttpResponse.json({ ...baseMarket, status: 'approved', proposed_outcome_id: 'o1', settleable: false, dispute_window_ends_at: endsAt }),
          ),
        )
        renderPage()
        expect(await screen.findByText(/disputes close in/i)).toBeInTheDocument()

        await act(() => vi.advanceTimersByTimeAsync(1001))

        expect(screen.getByText(/result is final; payout pending \(closed .+\)\./i)).toBeInTheDocument()
      } finally {
        vi.useRealTimers()
      }
    })
  })

  it('shows a reconnecting indicator when the connection drops', async () => {
    let wsClient: { close(code?: number): void } | null = null
    server.use(
      priceService.addEventListener('connection', ({ client }) => { wsClient = client }),
    )
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2% for December 2026?')
    await waitFor(() => expect(wsClient).not.toBeNull())
    act(() => wsClient!.close(1000))
    expect(await screen.findByText(/reconnecting to live prices/i)).toBeInTheDocument()
  })

  it('shows a degraded indicator when the origin is refused', async () => {
    let wsClient: { close(code?: number): void } | null = null
    server.use(
      priceService.addEventListener('connection', ({ client }) => { wsClient = client }),
    )
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2% for December 2026?')
    await waitFor(() => expect(wsClient).not.toBeNull())
    act(() => wsClient!.close(4403))
    expect(await screen.findByText(/live prices unavailable/i)).toBeInTheDocument()
  })

  // [FE][T-6] #61: placing a limit order and watching it rest, fill and be
  // cancelled, all on the market's own page.
  describe('limit orders', () => {
    function restingOrder(overrides: Record<string, unknown> = {}) {
      return {
        id: 'ord-1',
        market_id: 'mkt-abc',
        outcome_id: 'o1',
        outcome_position: 0,
        side: 'buy',
        limit_price: '0.3000',
        quantity: '50.0000',
        filled_quantity: '0.0000',
        average_fill_price: null,
        reserved_credits: '15.0000',
        reserved_shares: null,
        status: 'open',
        created_at: '2026-10-20T15:52:10.744443Z',
        cancelled_at: null,
        ...overrides,
      }
    }

    it('offers the Limit choice on a binary market', async () => {
      renderPage()
      expect(await screen.findByRole('button', { name: 'Limit' })).toBeInTheDocument()
    })

    it('hides the Limit choice on a market with more than two outcomes', async () => {
      // The ledger refuses one with market_not_binary, so the control is not
      // offered rather than offered and refused (ledger-service.md).
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.json({
          ...baseMarket,
          outcomes: [
            { id: 'o1', position: 0, label: 'Yes' },
            { id: 'o2', position: 1, label: 'No' },
            { id: 'o3', position: 2, label: 'Maybe' },
          ],
        })),
      )
      renderPage()
      await screen.findByText(baseMarket.question)
      expect(screen.queryByRole('button', { name: 'Limit' })).not.toBeInTheDocument()
    })

    it('shows the trader\'s resting order with its limit, quantity and filled-so-far', async () => {
      server.use(http.get(ORDERS, () => HttpResponse.json({ orders: [restingOrder({ filled_quantity: '20.5465' })] })))
      renderPage()

      expect(await screen.findByText('Your open orders')).toBeInTheDocument()
      const row = screen.getAllByRole('row')[1]
      expect(row).toHaveTextContent('0.3000')
      expect(row).toHaveTextContent('50.0000')
      expect(row).toHaveTextContent('20.5465')
    })

    it('reads no holdings on a closed market, even when the socket asks for a re-read', async () => {
      // The holdings only feed the sell clamp, and a closed market has no
      // sell to clamp. Its orders still re-read on every ack, since they stay
      // cancellable until the market has a result (ADR 0024).
      let holdingReads = 0
      let orderReads = 0
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.json({ ...baseMarket, status: 'closed' })),
        http.get(PORTFOLIO, () => {
          holdingReads += 1
          return HttpResponse.json(portfolio())
        }),
        http.get(ORDERS, () => {
          orderReads += 1
          return HttpResponse.json({ orders: [restingOrder()] })
        }),
        priceService.addEventListener('connection', ({ client }) => {
          client.addEventListener('message', event => {
            const message = JSON.parse(event.data as string)
            if (message.action === 'subscribe') {
              client.send(JSON.stringify({ type: 'subscribed', market_id: message.market_id }))
            }
          })
        }),
      )
      renderPage()

      // The mount's read and the ack's: the ack has been handled.
      await waitFor(() => expect(orderReads).toBe(2))
      await act(() => new Promise(resolve => setTimeout(resolve, 50)))

      expect(holdingReads).toBe(0)
    })

    it('says the open orders could not be loaded, and loads them on a retry', async () => {
      // Without this a failed first read looks exactly like having no
      // orders, and a trader with one resting cannot reach its cancel.
      let failing = true
      server.use(http.get(ORDERS, () => (failing ? HttpResponse.error() : HttpResponse.json({ orders: [restingOrder()] }))))
      const actor = userEvent.setup()
      renderPage()

      expect(await screen.findByRole('alert')).toHaveTextContent('Your open orders could not be loaded.')

      failing = false
      await actor.click(screen.getByRole('button', { name: 'Try again' }))

      expect(await screen.findByRole('button', { name: /^cancel buy yes/i })).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('shows no open-orders section when the trader has none here', async () => {
      // The list is scoped to this market: an order on another one belongs
      // on that market's page, not this one.
      server.use(http.get(ORDERS, () => HttpResponse.json({ orders: [restingOrder({ market_id: 'other-market' })] })))
      renderPage()
      await screen.findByText(baseMarket.question)
      expect(screen.queryByText('Your open orders')).not.toBeInTheDocument()
    })

    it('moves filled-so-far from an order frame, with no reload', async () => {
      // realtime-service.md, "Order updates": a fill reaches the owner on
      // every socket they have open, with no subscribe of its own.
      //
      // The page re-reads the list after a frame too. That read is held
      // here, so what moves the row is the frame and nothing else.
      let filled = false
      const reReadAfterFill = holdUntilReleased()
      let wsClient: { send(data: string): void } | null = null
      server.use(
        http.get(ORDERS, async () => {
          if (!filled) return HttpResponse.json({ orders: [restingOrder()] })
          await reReadAfterFill.held
          return HttpResponse.json({ orders: [restingOrder({ filled_quantity: '12.5000', reserved_credits: '11.2500' })] })
        }),
        priceService.addEventListener('connection', ({ client }) => {
          wsClient = client
          client.addEventListener('message', event => {
            const message = JSON.parse(event.data as string)
            if (message.action === 'subscribe') {
              client.send(JSON.stringify({ type: 'subscribed', market_id: message.market_id }))
            }
          })
        }),
      )
      renderPage()
      await screen.findByText('Your open orders')
      await waitFor(() => expect(screen.getAllByRole('row')[1]).toHaveTextContent('0.0000'))

      await waitFor(() => expect(wsClient).not.toBeNull())
      filled = true
      act(() => wsClient!.send(JSON.stringify({
        type: 'order',
        order_id: 'ord-1',
        market_id: 'mkt-abc',
        status: 'open',
        quantity: '50.0000',
        filled_quantity: '12.5000',
        fill: { transaction_id: 't1', quantity: '12.5000', total: '-3.7500', state_version: 7 },
        occurred_at: '2026-10-20T16:00:00Z',
      })))

      await waitFor(() => expect(screen.getAllByRole('row')[1]).toHaveTextContent('12.5000'))
      reReadAfterFill.release()
    })

    it('re-reads the list after an order frame, so what the order holds follows the fill', async () => {
      // The frame carries no money, and the ledger publishes it after the
      // commit that wrote the fill (realtime-service.md, "Publishing"), so a
      // read sent once it has arrived already shows the smaller reservation.
      let served = [restingOrder()]
      let wsClient: { send(data: string): void } | null = null
      server.use(
        http.get(ORDERS, () => HttpResponse.json({ orders: served })),
        priceService.addEventListener('connection', ({ client }) => {
          wsClient = client
        }),
      )
      renderPage()
      const row = () => screen.getByRole('row', { name: /buy yes/i })
      await waitFor(() => expect(row()).toHaveTextContent('15.0000 credits'))
      await waitFor(() => expect(wsClient).not.toBeNull())

      served = [restingOrder({ filled_quantity: '12.5000', average_fill_price: '0.2700', reserved_credits: '11.6250' })]
      act(() => wsClient!.send(JSON.stringify({
        type: 'order',
        order_id: 'ord-1',
        market_id: 'mkt-abc',
        status: 'open',
        quantity: '50.0000',
        filled_quantity: '12.5000',
        fill: { transaction_id: 't1', quantity: '12.5000', total: '-3.3750', state_version: 7 },
        occurred_at: '2026-10-20T16:00:00Z',
      })))

      await waitFor(() => expect(row()).toHaveTextContent('11.6250 credits'))
    })

    it('drops the order from the list once a frame says it filled in full', async () => {
      // The re-read that follows the frame is held, as above, so the frame
      // is what takes the row away.
      let filled = false
      const reReadAfterFill = holdUntilReleased()
      let wsClient: { send(data: string): void } | null = null
      server.use(
        http.get(ORDERS, async () => {
          if (!filled) return HttpResponse.json({ orders: [restingOrder()] })
          await reReadAfterFill.held
          return HttpResponse.json({ orders: [] })
        }),
        priceService.addEventListener('connection', ({ client }) => {
          wsClient = client
          client.addEventListener('message', event => {
            const message = JSON.parse(event.data as string)
            if (message.action === 'subscribe') {
              client.send(JSON.stringify({ type: 'subscribed', market_id: message.market_id }))
            }
          })
        }),
      )
      renderPage()
      await screen.findByText('Your open orders')

      await waitFor(() => expect(wsClient).not.toBeNull())
      filled = true
      act(() => wsClient!.send(JSON.stringify({
        type: 'order',
        order_id: 'ord-1',
        market_id: 'mkt-abc',
        status: 'filled',
        quantity: '50.0000',
        filled_quantity: '50.0000',
        fill: { transaction_id: 't2', quantity: '50.0000', total: '-15.0000', state_version: 8 },
        occurred_at: '2026-10-20T16:01:00Z',
      })))

      // The list holds live orders only, so a filled one leaves it — and
      // with the last one gone, so does the section.
      await waitFor(() => expect(screen.queryByText('Your open orders')).not.toBeInTheDocument())
      reReadAfterFill.release()
    })

    it('gives the trading panel what the sell orders on this market reserve, so it offers only the rest', async () => {
      // The join is the page's: the holdings come from the portfolio, the
      // reservations from the open-orders list. A buy reserves no shares, and
      // an order on another market belongs to that market's page.
      server.use(
        http.get(PORTFOLIO, () => HttpResponse.json(portfolio([{ market_id: 'mkt-abc', outcome_id: 'o1', quantity: '6.0000' }]))),
        http.get(ORDERS, () => HttpResponse.json({ orders: [
          restingOrder({ id: 'ord-1', side: 'sell', limit_price: '0.7000', quantity: '1.5000', reserved_credits: null, reserved_shares: '1.5000' }),
          restingOrder({ id: 'ord-2', side: 'sell', limit_price: '0.8000', quantity: '0.5000', reserved_credits: null, reserved_shares: '0.5000' }),
          restingOrder({ id: 'ord-3', market_id: 'mkt-other', side: 'sell', reserved_credits: null, reserved_shares: '3.0000' }),
          restingOrder({ id: 'ord-4' }),
        ] })),
      )
      const actor = userEvent.setup()
      renderPage()
      await actor.click(await screen.findByRole('button', { name: /^sell$/i }))

      await waitFor(() => expect(screen.getByLabelText('shares held')).toHaveTextContent('You can sell 4.0000 of your 6.0000 shares'))
    })

    it('re-reads the balance and the holdings, not only the orders, after a reconnect', async () => {
      // realtime-service.md, "Reconnecting": frames published while the
      // socket was away are gone, so fetch GET /ledger/orders/me and the
      // balance again. A fill missed in that gap moved credits and shares too.
      let wsClient: { close(code?: number): void } | null = null
      let snapshotReads = 0
      let holdingReads = 0
      server.use(
        http.get(SNAPSHOT, () => {
          snapshotReads += 1
          return HttpResponse.json({ error: { code: 'market_not_found', message: 'Not found.' } }, { status: 404 })
        }),
        http.get(PORTFOLIO, () => {
          holdingReads += 1
          return HttpResponse.json(portfolio())
        }),
        priceService.addEventListener('connection', ({ client }) => {
          wsClient = client
          client.addEventListener('message', event => {
            const message = JSON.parse(event.data as string)
            if (message.action === 'subscribe') {
              client.send(JSON.stringify({ type: 'subscribed', market_id: message.market_id }))
            }
          })
        }),
      )
      const refetchBalance = vi.fn(async () => {})
      renderPage('mkt-abc', { refetchBalance })

      // The mount's snapshot and the one after the first ack: the ack has
      // been handled once both are in.
      await waitFor(() => expect(snapshotReads).toBe(2))
      await waitFor(() => expect(holdingReads).toBeGreaterThan(0))
      const balanceReadsBefore = refetchBalance.mock.calls.length
      const holdingReadsBefore = holdingReads

      act(() => wsClient!.close(1000))
      // The reconnect's own ack, after the one-second backoff.
      await waitFor(() => expect(snapshotReads).toBe(3), { timeout: 3000 })

      expect(refetchBalance.mock.calls.length).toBeGreaterThan(balanceReadsBefore)
      await waitFor(() => expect(holdingReads).toBeGreaterThan(holdingReadsBefore))
    })

    it('keeps a refused cancel\'s message on screen when the re-read leaves no orders', async () => {
      // A refused cancel re-reads the list, and when the order was the
      // market's only one that read comes back empty. The message saying why
      // the cancel did nothing has to outlive the row it was about.
      let served = [restingOrder()]
      server.use(
        http.get(ORDERS, () => HttpResponse.json({ orders: served })),
        http.post(`${LEDGER_BASE}/ledger/orders/:id/cancel`, () => {
          served = []
          return HttpResponse.json({ error: { code: 'order_already_filled', message: 'too late' } }, { status: 409 })
        }),
      )
      const actor = userEvent.setup()
      renderPage()

      await actor.click(await screen.findByRole('button', { name: /^cancel buy yes/i }))

      // The re-read has landed once the row is gone.
      await waitFor(() => expect(screen.queryByRole('button', { name: /^cancel buy yes/i })).not.toBeInTheDocument())
      expect(screen.getByRole('alert')).toHaveTextContent('That order filled before the cancel reached it.')
    })
  })
})

describe('MarketDetailPage — the browser tab', () => {
  beforeEach(() => {
    document.title = "PredictSMU — SMU's prediction market"
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.json(baseMarket)),
      http.get(SNAPSHOT, () =>
        HttpResponse.json({ error: { code: 'market_not_found', message: 'Not found.' } }, { status: 404 }),
      ),
    )
  })

  it('names the tab after the question once it has loaded', async () => {
    // This page's heading is the question rather than a PageTitle, so it is
    // the one page that has to title itself (#356).
    renderPage()

    await screen.findByText(baseMarket.question)

    expect(document.title).toBe(`${baseMarket.question} · PredictSMU`)
  })
})
