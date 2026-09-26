import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { server } from '../test/server'
import MarketDetailPage from './MarketDetailPage'

const MARKET_BASE = 'http://localhost:8001'

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

// Stub WebSocket — tests don't exercise live prices; that's in useMarketPrices tests.
const mockWs = {
  send: vi.fn(),
  close: vi.fn(),
  onopen: null as (() => void) | null,
  onmessage: null as ((e: { data: string }) => void) | null,
  readyState: 1,
}
vi.stubGlobal('WebSocket', vi.fn(() => mockWs))

function renderPage(marketId = 'mkt-abc') {
  return render(
    <AuthContext.Provider value={{ user: trader, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={[`/markets/${marketId}`]}>
        <Routes>
          <Route path="/markets/:id" element={<MarketDetailPage />} />
          <Route path="/markets" element={<p>markets list</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('MarketDetailPage', () => {
  beforeEach(() => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets/:id`, () =>
        HttpResponse.json(baseMarket),
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

  it('shows outcome cards with no-trades placeholder when no price events received', async () => {
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2% for December 2026?')
    expect(screen.getByLabelText('Yes price')).toHaveTextContent('—')
    expect(screen.getByLabelText('No price')).toHaveTextContent('—')
    expect(screen.getAllByText('no trades yet').length).toBe(2)
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
})
