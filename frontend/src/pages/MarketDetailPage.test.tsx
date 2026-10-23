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
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage(marketId = 'mkt-abc') {
  return render(
    <WithProviders user={trader}>
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

    expect(await screen.findByText("You don't hold any Yes shares.")).toBeInTheDocument()

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
