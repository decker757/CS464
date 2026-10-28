import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse, ws } from 'msw'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'
import { afterEach, beforeAll, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'
import MarketCard from '../components/markets/MarketCard'
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
const PRICE_HISTORY = `${LEDGER_BASE}/ledger/markets/:id/price-history`

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
// (#357). `position` carries only the fields the page reads: the three it
// joins on, and `reserved_shares` where a test holds some back.
function portfolio(
  positions: { market_id: string; outcome_id: string; quantity: string; reserved_shares?: string }[] = [],
) {
  return {
    user_id: '1',
    account_id: 'acct-1',
    balance: '1000.0000',
    positions_value: '0.0000',
    net_worth: '1000.0000',
    // `reserved_shares` defaults to nothing held back, and is never null on a
    // position (ledger-service.md) — unlike the same name on an order.
    positions: positions.map(p => ({ reserved_shares: '0.0000', ...p, outcome_position: 0, cost_basis: '0.0000', average_entry_price: '0.5000', price: '0.5000', value: '0.0000', unrealized_pnl: '0.0000', result: null, payout: null, state_version: 1 })),
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
// every test that renders a tradeable market makes it, this one included. So
// is the price history (#453), which every market the page shows reads: a
// market with no book yet, unless a test serves one.
beforeEach(() => {
  server.use(
    priceService.addEventListener('connection', () => { /* silent */ }),
    http.get(PORTFOLIO, () => HttpResponse.json(portfolio())),
    http.get(PRICE_HISTORY, ({ params }) => HttpResponse.json(priceHistory(String(params.id), []))),
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

// One point of GET .../price-history: the price after a trade, or the book's
// opening at state_version 0 (ledger-service.md).
function pricePoint(version: number, occurredAt: string, yes: string, no: string) {
  return {
    state_version: version,
    occurred_at: occurredAt,
    prices: [
      { outcome_id: 'o1', position: 0, price: yes },
      { outcome_id: 'o2', position: 1, price: no },
    ],
  }
}

function priceHistory(marketId: string, points: ReturnType<typeof pricePoint>[]) {
  return { market_id: marketId, points, result: null, winning_outcome_id: null, result_recorded_at: null }
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

  // The card's words, not a future closing date (#429): the badge and the
  // close label both say Closed, as they do on the market's browse card.
  it('says Closed, not a future closing date, for a market an admin closed', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status: 'closed' }),
      ),
    )
    renderPage()
    await screen.findByText(baseMarket.question)
    expect(screen.getAllByText('Closed')).toHaveLength(2)
    expect(screen.queryByText(/^Closes /)).not.toBeInTheDocument()
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

  // #429's review: with the header reading "Closed", About this market said
  // "Closes 5 Jan 2099" under it for a market an admin had closed early — an
  // early close leaves close_time in the future. Once trading has stopped the
  // date is the schedule the market was published with, not a close still to
  // come. Stopped is the clock rule (ADR 0011), so the third case is still
  // `open` in the status column, inside the sweep window.
  it.each([
    { name: 'an open market', status: 'open', closeTime: '2099-01-05T12:00:00Z', label: 'Closes', date: /5 Jan 2099/ },
    { name: 'a market an admin closed early', status: 'closed', closeTime: '2099-01-05T12:00:00Z', label: 'Scheduled to close', date: /5 Jan 2099/ },
    { name: 'a market the clock has closed and the sweep has not', status: 'open', closeTime: '2025-08-09T12:00:00Z', label: 'Scheduled to close', date: /9 Aug 2025/ },
  ])('labels the close date under About as "$label" for $name', async ({ status, closeTime, label, date }) => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.json({ ...baseMarket, status, close_time: closeTime })),
    )
    renderPage()
    await screen.findByText(baseMarket.question)

    expect(screen.getByText(label, { exact: true }).nextElementSibling).toHaveTextContent(date)
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
      // directly above the line asserted above. The close label beside the
      // badge is the second Closed (#429).
      expect(screen.getAllByText('Closed')).toHaveLength(2)
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

  // #429 point 1: the browse card said "Closes in 23m" while this page said
  // "Closes 21 Oct 2026, 01:24 pm" for the same market at the same minute. The
  // clock is the authority (ADR 0011), so the screen a trader commits money on
  // counts down the way the card does, and keeps counting.
  describe('the close countdown', () => {
    const NOW = new Date('2026-10-18T12:00:00Z')
    const fromNow = (ms: number) => new Date(NOW.getTime() + ms).toISOString()

    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      vi.setSystemTime(NOW)
    })

    afterEach(() => {
      vi.useRealTimers()
    })

    function serveMarket(overrides: Record<string, unknown>) {
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.json({ ...baseMarket, ...overrides })),
      )
    }

    // Ten seconds across a minute boundary, the card's tick. A full minute
    // would also pass on the dispute-window timer's once-a-minute render,
    // which is not the clock this page's countdown is meant to keep. The
    // first two seconds let the page's other reads land, since any render
    // they cause reads the clock too; the label has not moved by the end of
    // them, so what moves it after is the tick.
    it('counts the close down as time passes rather than freezing at first render', async () => {
      serveMarket({ close_time: fromNow((3 * 60 + 12) * 60_000 + 8_000) })
      renderPage()
      expect(await screen.findByText('Closes in 3h 12m')).toBeInTheDocument()
      await act(() => vi.advanceTimersByTimeAsync(2_000))
      expect(screen.getByText('Closes in 3h 12m')).toBeInTheDocument()

      await act(() => vi.advanceTimersByTimeAsync(8_000))

      expect(screen.getByText('Closes in 3h 11m')).toBeInTheDocument()
    })

    // The DoD's first item: the card and this page, given the same market and
    // the same clock, say the same thing about its close — near, far, and
    // after trading has stopped.
    it.each([
      { name: 'an open market closing within two days', status: 'open' as const, closeTime: fromNow((3 * 60 + 12) * 60_000 + 30_000), label: 'Closes in 3h 12m' },
      { name: 'an open market closing in three days', status: 'open' as const, closeTime: '2026-10-21T12:00:00Z', label: 'Closes 21 Oct 2026' },
      { name: 'an approved market', status: 'approved' as const, closeTime: fromNow(-60_000), label: 'Closed' },
    ])('reads "$label" on both the card and the page for $name', async ({ status, closeTime, label }) => {
      serveMarket({ status, close_time: closeTime, proposed_outcome_id: status === 'approved' ? 'o1' : null, dispute_window_ends_at: null })
      const { id, question, outcomes } = baseMarket
      const card = render(
        <MemoryRouter>
          <MarketCard market={{ id, status, question, close_time: closeTime, outcomes }} />
        </MemoryRouter>,
      )
      const page = renderPage()

      expect(await within(page.container).findByText(label)).toBeInTheDocument()
      expect(within(card.container).getByText(label)).toBeInTheDocument()
    })

    // The panel locks at close_time on a timer of its own (#36). A label on a
    // ten-second tick would go on saying "Closes in under a minute" beside
    // "Trading is closed" until its next tick came round.
    it('stops counting at the instant the trading panel locks', async () => {
      serveMarket({ close_time: fromNow(5_000) })
      renderPage()
      expect(await screen.findByText('Closes in under a minute')).toBeInTheDocument()

      await act(() => vi.advanceTimersByTimeAsync(5_000))

      expect(screen.getByText(/trading is closed/i)).toBeInTheDocument()
      expect(screen.queryByText('Closes in under a minute')).not.toBeInTheDocument()
    })
  })

  // #429 point 3: an approved market read "Approved outcome: Yes" above YES
  // 48.8% and NO 51.3%, each labelled "implied probability" — the answer is
  // known, and the largest thing on the page said the market thought it
  // unlikely. Past PENDING_RESOLUTION the result takes the price's place.
  describe('a market with a decided result', () => {
    it.each(['approved', 'settled'])('shows the result in place of the price once the market is %s', async status => {
      let snapshotServed = false
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () =>
          HttpResponse.json({ ...baseMarket, status, close_time: '2026-01-05T12:00:00Z', proposed_outcome_id: 'o1', dispute_window_ends_at: null }),
        ),
        http.get(SNAPSHOT, () => {
          snapshotServed = true
          return HttpResponse.json(priceFrame(9, '0.4880', '0.5130'))
        }),
      )
      renderPage()

      expect(await screen.findByLabelText('Yes result')).toHaveTextContent('Won')
      expect(screen.getByLabelText('No result')).toHaveTextContent('Lost')
      // The last trade's price has reached the page by now; it must not be
      // what the page shows, under any caption.
      await waitFor(() => expect(snapshotServed).toBe(true))
      await act(() => new Promise(resolve => setTimeout(resolve, 50)))
      expect(screen.queryByText('implied probability')).not.toBeInTheDocument()
      expect(screen.queryByText('48.8%')).not.toBeInTheDocument()
      expect(screen.queryByText('51.3%')).not.toBeInTheDocument()
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

    // #410: the panel offers `quantity - reserved_shares`, and both figures
    // come from the portfolio row. The ledger sums the trader's live sell
    // orders itself, in the statement that reads the position — so the page no
    // longer adds up the orders list, and the orders here deliberately say a
    // different thing from the portfolio to prove which one is read.
    it('offers only the part of the holding the portfolio says is not reserved', async () => {
      server.use(
        http.get(PORTFOLIO, () => HttpResponse.json(portfolio([
          { market_id: 'mkt-abc', outcome_id: 'o1', quantity: '6.0000', reserved_shares: '2.0000' },
        ]))),
        // Were the page still summing these, it would offer 4.5000.
        http.get(ORDERS, () => HttpResponse.json({ orders: [
          restingOrder({ id: 'ord-1', side: 'sell', limit_price: '0.7000', quantity: '1.5000', reserved_credits: null, reserved_shares: '1.5000' }),
        ] })),
      )
      const actor = userEvent.setup()
      renderPage()
      await actor.click(await screen.findByRole('button', { name: /^sell$/i }))

      await waitFor(() => expect(screen.getByLabelText('shares held')).toHaveTextContent('You can sell 4.0000 of your 6.0000 shares'))
    })

    // #451 / #478: a trader whose shares are all held back still holds them,
    // so the line says so rather than "You don't hold any", which would offer
    // to buy shares they already own.
    it('says a fully reserved holding can sell nothing, not that none is held', async () => {
      server.use(
        http.get(PORTFOLIO, () => HttpResponse.json(portfolio([
          { market_id: 'mkt-abc', outcome_id: 'o1', quantity: '6.0000', reserved_shares: '6.0000' },
        ]))),
      )
      const actor = userEvent.setup()
      renderPage()
      await actor.click(await screen.findByRole('button', { name: /^sell$/i }))

      await waitFor(() => expect(screen.getByLabelText('shares held')).toHaveTextContent('You can sell 0.0000 of your 6.0000 shares'))
      expect(screen.queryByText(/don't hold any/i)).not.toBeInTheDocument()
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

// [FE][T-5] #453: the history a trader reads before deciding, from the route
// docs/api/ledger-service.md opens to any role. The chart's own drawing is
// PriceHistoryChart.test.tsx's job; these are about what this page hands it,
// and what it shows instead when there is nothing to draw.
describe('MarketDetailPage — price history', () => {
  const THIS_MARKETS_HISTORY = `${LEDGER_BASE}/ledger/markets/mkt-abc/price-history`
  const NO_TRADES_YET = 'No trades yet. The price has not moved since this market opened.'

  const threeTrades = [
    pricePoint(0, '2026-09-13T14:12:03Z', '0.5000', '0.5000'),
    pricePoint(1, '2026-09-14T09:00:00Z', '0.6225', '0.3775'),
    pricePoint(2, '2026-09-15T09:00:00Z', '0.5744', '0.4256'),
  ]

  // The chart is a lazy chunk, and the first test to draw it would otherwise
  // pay for loading recharts inside findByText's one-second window: on a busy
  // machine it missed. Loaded once here, every test below finds the module
  // already in the cache, and what each one waits on is the page alone.
  beforeAll(async () => {
    await import('../components/markets/PriceHistoryChart')
  })

  beforeEach(() => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.json(baseMarket)),
      http.get(SNAPSHOT, () =>
        HttpResponse.json({ error: { code: 'market_not_found', message: 'Not found.' } }, { status: 404 }),
      ),
    )
  })

  it('charts the price history of the market being traded', async () => {
    // Served for this market's id only: any other id gets the empty default.
    server.use(http.get(THIS_MARKETS_HISTORY, () => HttpResponse.json(priceHistory('mkt-abc', threeTrades))))

    renderPage()

    expect(await screen.findByText(/showing all 3 price points/i)).toBeInTheDocument()
    const legend = screen.getByRole('list', { name: 'Outcomes' })
    expect(within(legend).getByText('Yes')).toBeInTheDocument()
    expect(within(legend).getByText('No')).toBeInTheDocument()
  })

  // [T-5] #495's first criterion: the chart adds a point for every live price
  // frame. Before this, Alice's price cards moved on Bob's trade and her chart
  // did not, with nothing to say it was behind.
  it('adds a point when another trader moves the price', async () => {
    let wsClient: { send(data: string): void } | null = null
    server.use(
      http.get(THIS_MARKETS_HISTORY, () => HttpResponse.json(priceHistory('mkt-abc', threeTrades))),
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
    expect(await screen.findByText(/showing all 3 price points/i)).toBeInTheDocument()

    // Somebody else's trade, arriving as a frame on this page's socket.
    await act(async () => {
      wsClient?.send(JSON.stringify({
        type: 'price',
        market_id: 'mkt-abc',
        state_version: 3,
        prices: [
          { outcome_id: 'o1', position: 0, price: '0.7000' },
          { outcome_id: 'o2', position: 1, price: '0.3000' },
        ],
        occurred_at: '2026-09-16T09:00:00Z',
      }))
    })

    expect(await screen.findByText(/showing all 4 price points/i)).toBeInTheDocument()
  })

  // "A point for every live price frame", not only the newest: the socket
  // hook keeps one snapshot per market, so a chart that drew only that
  // snapshot lost the first of two trades the moment the second arrived.
  it('keeps a point for each of two trades by others', async () => {
    let wsClient: { send(data: string): void } | null = null
    server.use(
      http.get(THIS_MARKETS_HISTORY, () => HttpResponse.json(priceHistory('mkt-abc', threeTrades))),
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
    expect(await screen.findByText(/showing all 3 price points/i)).toBeInTheDocument()

    act(() => wsClient!.send(JSON.stringify({ type: 'price', ...priceFrame(3, '0.7000', '0.3000') })))
    expect(await screen.findByText(/showing all 4 price points/i)).toBeInTheDocument()

    act(() => wsClient!.send(JSON.stringify({ type: 'price', ...priceFrame(4, '0.8000', '0.2000') })))
    expect(await screen.findByText(/showing all 5 price points/i)).toBeInTheDocument()
  })

  // #495's second: a frame whose state_version the chart already holds is
  // skipped, so a refetch and the frames around it never draw a trade twice.
  it('ignores a frame for a trade already on the chart', async () => {
    let wsClient: { send(data: string): void } | null = null
    server.use(
      http.get(THIS_MARKETS_HISTORY, () => HttpResponse.json(priceHistory('mkt-abc', threeTrades))),
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
    expect(await screen.findByText(/showing all 3 price points/i)).toBeInTheDocument()

    // state_version 2 is the newest point the history already carries. A
    // later `occurred_at` must not make it look like a new trade: the same
    // trade carries one time on its point and another on its frame
    // (ledger-service.md, "Dedupe on state_version, never on time").
    await act(async () => {
      wsClient?.send(JSON.stringify({
        type: 'price',
        market_id: 'mkt-abc',
        state_version: 2,
        prices: [
          { outcome_id: 'o1', position: 0, price: '0.5744' },
          { outcome_id: 'o2', position: 1, price: '0.4256' },
        ],
        occurred_at: '2026-09-15T09:00:09Z',
      }))
    })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })

    expect(screen.getByText(/showing all 3 price points/i)).toBeInTheDocument()
  })

  // #495's third: frames published while a dropped connection was away are
  // gone (ADR 0010), so a reconnect re-reads the history rather than trusting
  // the frames it missed.
  it('re-reads the history after a reconnect', async () => {
    let reads = 0
    let dropped = false
    let wsClient: { close(code?: number): void } | null = null
    server.use(
      http.get(THIS_MARKETS_HISTORY, () => {
        reads += 1
        // Only a read after the drop sees the trade made while the socket was
        // away. Keyed on the drop and not on the read count, because the
        // first ack re-reads too: a count would hand that read the trade, and
        // the test would pass with no re-read on the reconnect at all.
        const points = dropped ? [...threeTrades, pricePoint(3, '2026-09-16T09:00:00Z', '0.7000', '0.3000')] : threeTrades
        return HttpResponse.json(priceHistory('mkt-abc', points))
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
    // The mount's read and the first ack's, both before the drop.
    await waitFor(() => expect(reads).toBe(2))
    expect(await screen.findByText(/showing all 3 price points/i)).toBeInTheDocument()

    // An abnormal close, so the hook reconnects and subscribes again.
    dropped = true
    act(() => wsClient!.close(1006))

    // The first retry waits one second. Three is room for it and still under
    // the test's own five, so a missing re-read fails here and not as a timeout.
    expect(await screen.findByText(/showing all 4 price points/i, undefined, { timeout: 3000 })).toBeInTheDocument()
  })

  it('charts the history of a market that has stopped trading', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () => HttpResponse.json({ ...baseMarket, status: 'settled' })),
      http.get(THIS_MARKETS_HISTORY, () => HttpResponse.json(priceHistory('mkt-abc', threeTrades))),
    )

    renderPage()

    expect(await screen.findByText(/showing all 3 price points/i)).toBeInTheDocument()
  })

  it('says nobody has traded, rather than charting the opening price alone', async () => {
    // One point is the book's opening and nothing else: a chart of it is a
    // lone dot and a range control with nothing to narrow.
    server.use(http.get(THIS_MARKETS_HISTORY, () =>
      HttpResponse.json(priceHistory('mkt-abc', [pricePoint(0, '2026-09-13T14:12:03Z', '0.5000', '0.5000')])),
    ))

    renderPage()

    expect(await screen.findByText(NO_TRADES_YET)).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Time range' })).not.toBeInTheDocument()
  })

  it('says nobody has traded on a market the ledger has no book for yet', async () => {
    // The default handler's empty history: no point at all, not even the opening.
    renderPage()

    expect(await screen.findByText(NO_TRADES_YET)).toBeInTheDocument()
  })

  it('reads the history again after the trader\'s own trade, so a first trade is charted', async () => {
    // "No trades yet" is false the moment this trader has traded, and the
    // page has nothing else that would ever read the history again.
    let traded = false
    server.use(
      http.get(THIS_MARKETS_HISTORY, () => HttpResponse.json(priceHistory('mkt-abc', traded
        ? [pricePoint(0, '2026-09-13T14:12:03Z', '0.5000', '0.5000'), pricePoint(1, '2026-09-13T15:00:00Z', '0.5250', '0.4750')]
        : []))),
      http.get(PREVIEW, () => HttpResponse.json(tradeQuote())),
      http.post(TRADES, () => {
        traded = true
        return HttpResponse.json(tradeResult(), { status: 201 })
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    expect(await screen.findByText(NO_TRADES_YET)).toBeInTheDocument()

    await actor.type(await screen.findByLabelText('Quantity (shares)'), '10')
    const buy = screen.getByRole('button', { name: /^buy yes$/i })
    await waitFor(() => expect(buy).toBeEnabled())
    await actor.click(buy)
    await screen.findByText(/bought/i)

    expect(await screen.findByText(/showing all 2 price points/i)).toBeInTheDocument()
    expect(screen.queryByText(NO_TRADES_YET)).not.toBeInTheDocument()
  })

  it('reads the history again after the trader\'s own limit order fills on arrival', async () => {
    // A marketable placement fills inside the same request, as an ordinary
    // buy against the book (ADR 0024), so the history gains a point exactly
    // as it does for a market trade.
    let placed = false
    server.use(
      http.get(THIS_MARKETS_HISTORY, () => HttpResponse.json(priceHistory('mkt-abc', placed
        ? [pricePoint(0, '2026-09-13T14:12:03Z', '0.5000', '0.5000'), pricePoint(1, '2026-09-13T15:00:00Z', '0.5250', '0.4750')]
        : []))),
      http.post(`${LEDGER_BASE}/ledger/markets/:id/orders`, () => {
        placed = true
        return HttpResponse.json({
          order: {
            id: 'ord-1',
            market_id: 'mkt-abc',
            outcome_id: 'o1',
            outcome_position: 0,
            side: 'buy',
            limit_price: '0.9000',
            quantity: '10.0000',
            filled_quantity: '10.0000',
            average_fill_price: '0.5125',
            reserved_credits: '0.0000',
            reserved_shares: null,
            status: 'filled',
            created_at: '2026-09-13T15:00:00Z',
            cancelled_at: null,
          },
          fills: [{ transaction_id: 't1', quantity: '10.0000', total: '-5.1250', state_version: 1 }],
          book_state_version: 1,
        }, { status: 201 })
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    expect(await screen.findByText(NO_TRADES_YET)).toBeInTheDocument()

    await actor.click(await screen.findByRole('button', { name: 'Limit' }))
    await actor.type(screen.getByLabelText('Limit price'), '0.9')
    await actor.type(screen.getByLabelText('Quantity (shares)'), '10')
    await actor.click(screen.getByRole('button', { name: /^place buy order$/i }))
    await screen.findByText(/filled in full/i)

    expect(await screen.findByText(/showing all 2 price points/i)).toBeInTheDocument()
    expect(screen.queryByText(NO_TRADES_YET)).not.toBeInTheDocument()
  })

  it('shows the trading panel while the price history is still on its way', async () => {
    // The panel is the point of the page (#453): it must not wait on the read.
    const { held, release } = holdUntilReleased()
    server.use(http.get(THIS_MARKETS_HISTORY, async () => {
      await held
      return HttpResponse.json(priceHistory('mkt-abc', threeTrades))
    }))

    renderPage()

    expect(await screen.findByRole('button', { name: /^buy$/i })).toBeInTheDocument()
    expect(screen.getByText('Loading price history…')).toBeInTheDocument()

    release()
    expect(await screen.findByText(/showing all 3 price points/i)).toBeInTheDocument()
  })

  it('says the history failed and keeps the trading panel when the read fails', async () => {
    server.use(http.get(THIS_MARKETS_HISTORY, () =>
      HttpResponse.json({ error: { code: 'internal_error', message: 'Boom.' } }, { status: 500 }),
    ))

    renderPage()

    expect(await screen.findByText('Failed to load price history.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load price history.')
    expect(screen.getByRole('button', { name: /^buy$/i })).toBeInTheDocument()
  })

  it('keeps the trading panel when the chart itself fails, and says the history is unavailable', async () => {
    // The likeliest fault is the chart's chunk failing to download after a
    // deploy, which only a browser can do: price-history.e2e.ts aborts it.
    // Here the chart throws while drawing a point with no prices, which
    // reaches the same place: toChartRows (utils/priceHistoryChart.ts)
    // iterates point.prices, and null is not iterable. Without a boundary of its own, the throw takes
    // the whole page down with it, trading panel included.
    const quietReactsReport = vi.spyOn(console, 'error').mockImplementation(() => {})
    onTestFinished(() => quietReactsReport.mockRestore())
    // Not through priceHistory(), whose type would refuse the broken point.
    const brokenPoint = { ...threeTrades[1], prices: null }
    server.use(http.get(THIS_MARKETS_HISTORY, () =>
      HttpResponse.json({ ...priceHistory('mkt-abc', []), points: [threeTrades[0], brokenPoint] }),
    ))

    renderPage()

    expect(await screen.findByText('Price history unavailable. Reload the page to try again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^buy yes$/i })).toBeInTheDocument()
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
