import { act, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse, ws } from 'msw'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../context/AuthContext'
import { server } from '../test/server'
import { WithProviders } from '../test/renderWithProviders'
import MarketDetailPage from './MarketDetailPage'

const MARKET_BASE = 'http://localhost:8001'
const SNAPSHOT = 'http://localhost:8003/ledger/markets/:id/snapshot'

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

// MSW WebSocket link for the realtime price socket. Silently accepts
// connections so the hook connects without errors; indicator tests drive
// the close from the server side.
const priceService = ws.link('ws://localhost:8004/ws/prices')

// Stub WebSocket — most tests don't exercise live prices (that's in
// useMarketPrices tests). A silent MSW handler keeps the hook happy.
beforeEach(() => {
  server.use(priceService.addEventListener('connection', () => { /* silent */ }))
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

  it('shows trading controls for open markets', async () => {
    renderPage()
    expect(await screen.findByRole('button', { name: /buy yes/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /buy no/i })).toBeInTheDocument()
    expect(screen.getByText('Trading coming soon.')).toBeInTheDocument()
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

  it('shows the winning outcome for settled markets', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status: 'approved', proposed_outcome_id: 'o1' }),
      ),
    )
    renderPage()
    // The settled banner contains "Settled: Yes" — use the strong element for the winner label
    const banner = await screen.findByText(/settled:/i)
    expect(banner).toBeInTheDocument()
    expect(banner.querySelector('strong')).toHaveTextContent('Yes')
  })

  // #12 adds a `settled` status the frontend does not know yet; the page must
  // still render rather than go blank on an unknown value.
  it('shows a market whose status the page does not know yet', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json({ ...baseMarket, status: 'settled' }),
      ),
    )
    renderPage()
    expect(await screen.findByText(baseMarket.question)).toBeInTheDocument()
    expect(screen.getByText('settled')).toBeInTheDocument()
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

    it('shows trading closed for an open market whose close time has passed', async () => {
      server.use(
        http.get(`${MARKET_BASE}/public/markets/:id`, () =>
          HttpResponse.json({ ...baseMarket, close_time: '2025-08-09T12:00:00Z' }),
        ),
      )
      renderPage()
      expect(await screen.findByText(/trading is closed/i)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /buy/i })).not.toBeInTheDocument()
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
