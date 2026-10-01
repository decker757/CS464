import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { BalanceContext } from '../context/BalanceContext'
import { server } from '../test/server'
import PortfolioPage from './PortfolioPage'

const LEDGER_BASE = 'http://localhost:8003'
const MARKET_BASE = 'http://localhost:8001'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

function portfolio(overrides: Record<string, unknown> = {}) {
  return {
    user_id: '1',
    account_id: 'acc-1',
    balance: '997.4687',
    positions_value: '2.5312',
    net_worth: '999.9999',
    positions: [
      {
        market_id: 'mkt-1',
        outcome_id: 'o-yes',
        outcome_position: 0,
        quantity: '5.0000',
        cost_basis: '2.5625',
        average_entry_price: '0.5125',
        price: '0.5125',
        value: '2.5312',
        unrealized_pnl: '-0.0313',
        state_version: 2,
      },
    ],
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

function mockPortfolio(body: Record<string, unknown>) {
  server.use(http.get(`${LEDGER_BASE}/ledger/portfolio/me`, () => HttpResponse.json(body)))
}

function mockMarket(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${MARKET_BASE}/public/markets/${id}`, () => HttpResponse.json(body)))
}

function renderPage() {
  return render(
    <AuthContext.Provider value={{ user: trader, login: () => {}, logout: async () => {} }}>
      <BalanceContext.Provider value={{ balance: undefined, refetch: async () => {} }}>
        <MemoryRouter initialEntries={['/portfolio']}>
          <Routes>
            <Route path="/portfolio" element={<PortfolioPage />} />
            <Route path="/markets/:id" element={<p>market detail</p>} />
          </Routes>
        </MemoryRouter>
      </BalanceContext.Provider>
    </AuthContext.Provider>,
  )
}

describe('PortfolioPage', () => {
  it('shows cash, positions value and net worth as whole numbers', async () => {
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('997 credits')).toBeInTheDocument()
    expect(screen.getByText('2 credits')).toBeInTheDocument()
    expect(screen.getByText('999 credits')).toBeInTheDocument()
  })

  it('shows each position with its market question and outcome label', async () => {
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('5.0000')).toBeInTheDocument()
  })

  it('shows unrealized P&L in red with a minus sign for a loss', async () => {
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    const pnl = await screen.findByText('-0.0313')
    expect(pnl).toHaveClass('text-danger')
  })

  it('shows unrealized P&L in green with a plus sign for a gain', async () => {
    mockPortfolio(portfolio({ positions: [{ ...portfolio().positions[0], unrealized_pnl: '1.5000' }] }))
    mockMarket('mkt-1', market())
    renderPage()

    const pnl = await screen.findByText('+1.5000')
    expect(pnl).toHaveClass('text-success')
  })

  it('fetches each distinct market only once across multiple positions in it', async () => {
    let calls = 0
    server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => { calls += 1; return HttpResponse.json(market()) }))
    mockPortfolio(portfolio({
      positions: [
        { ...portfolio().positions[0], outcome_id: 'o-yes' },
        { ...portfolio().positions[0], outcome_id: 'o-no' },
      ],
    }))
    renderPage()

    await screen.findAllByText('Will it rain in Singapore tomorrow?')
    expect(calls).toBe(1)
  })

  it('links a position to its market detail page', async () => {
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    const link = await screen.findByRole('link', { name: /will it rain/i })
    expect(link).toHaveAttribute('href', '/markets/mkt-1')
  })

  it('shows an empty state when there are no positions', async () => {
    mockPortfolio(portfolio({ positions: [] }))
    renderPage()

    expect(await screen.findByText(/no open positions/i)).toBeInTheDocument()
  })

  it('shows an error when the portfolio fails to load', async () => {
    server.use(http.get(`${LEDGER_BASE}/ledger/portfolio/me`, () => HttpResponse.error()))
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })
})
