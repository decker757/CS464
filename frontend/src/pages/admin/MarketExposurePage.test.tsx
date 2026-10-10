import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import MarketExposurePage from './MarketExposurePage'

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

function mockMarket(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${MARKET_BASE}/public/markets/${id}`, () => HttpResponse.json(body)))
}

function mockExposure(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${LEDGER_BASE}/ledger/markets/${id}/exposure`, () => HttpResponse.json({ market_id: id, ...body })))
}

function renderPage(marketId = 'mkt-1') {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={[`/admin/markets/${marketId}/exposure`]}>
        <Routes>
          <Route path="/admin/markets/:id/exposure" element={<MarketExposurePage />} />
          <Route path="/admin/markets" element={<p>all markets</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('MarketExposurePage', () => {
  it('shows the pool and the per-outcome exposure table', async () => {
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      pool: '1250.0000',
      exceeds_pool: false,
      outcomes: [
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', max_payout: '120.0000' },
        { outcome_id: 'o-no', position: 1, shares_outstanding: '45.0000', max_payout: '45.0000' },
      ],
    })

    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(await screen.findByText('1,250.0000')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByText(/exceeds the pool/i)).not.toBeInTheDocument()
  })

  it('flags when an outcome\'s max payout exceeds the pool', async () => {
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      pool: '100.0000',
      exceeds_pool: true,
      outcomes: [
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '500.0000', max_payout: '500.0000' },
      ],
    })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/exceeds the pool/i)
  })

  it('shows a 200-with-nothing market as no trades yet, not an error', async () => {
    // #6's notes: a market the ledger has never opened a book for reads as
    // 200 with no outcomes, null figures and the flag down.
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', { pool: null, exceeds_pool: false, outcomes: [] })

    renderPage()

    expect(await screen.findByText(/no trades yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows an error when the market fails to load', async () => {
    server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => HttpResponse.error()))
    mockExposure('mkt-1', { pool: null, exceeds_pool: false, outcomes: [] })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load this market/i)
  })

  it('shows an error when exposure fails to load, independent of the market', async () => {
    mockMarket('mkt-1', market())
    server.use(http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/exposure`, () => HttpResponse.error()))

    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load exposure/i)
  })

  it('shows a link back to all markets', async () => {
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', { pool: null, exceeds_pool: false, outcomes: [] })
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain in Singapore tomorrow?')

    await actor.click(screen.getByRole('link', { name: '← All Markets' }))

    expect(screen.getByText('all markets')).toBeInTheDocument()
  })
})
