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

// A real portfolio, confirmed against the live ledger: a trader buys 5 Yes
// on a market with liquidity_b 100 and nothing else traded against it.
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
        cost_basis: '2.5313',
        average_entry_price: '0.5063',
        price: '0.5125',
        value: '2.5312',
        unrealized_pnl: '-0.0001',
        result: null,
        payout: null,
        state_version: 1,
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
  it('shows cash, positions value and net worth at full precision, so they add up', async () => {
    // formatCredits alone would truncate each to a whole number - 997, 2 and
    // 999 - which do not sum correctly and would show "0 credits" for a
    // positions value under 1.
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('997.4687 credits')).toBeInTheDocument()
    expect(screen.getByText('2.5312 credits')).toBeInTheDocument()
    expect(screen.getByText('999.9999 credits')).toBeInTheDocument()
  })

  it('shows each position with its market question and outcome label', async () => {
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('5.0000')).toBeInTheDocument()
  })

  it('shows the price as a percentage, like the market page does', async () => {
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    await screen.findByText('Will it rain in Singapore tomorrow?')
    expect(screen.getByText('51.3%')).toBeInTheDocument()
    expect(screen.queryByText('0.5125')).not.toBeInTheDocument()
  })

  it('shows average entry as credits per share, not a percentage', async () => {
    // #247: average_entry_price is credits per share and can pass 1.0000 on
    // a skewed book (ledger-service.md), so it is shown through the same
    // formatter as every other credits figure, never through formatPrice.
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    await screen.findByText('Will it rain in Singapore tomorrow?')
    expect(screen.getByText('0.5063')).toBeInTheDocument()
    expect(screen.queryByText('50.6%')).not.toBeInTheDocument()
  })

  it('shows cost basis distinctly from value and unrealized P&L', async () => {
    // [T-4] #24's AC lists cost basis as one of the fields shown per
    // position, alongside quantity, average entry, price, value and P&L.
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    await screen.findByText('Will it rain in Singapore tomorrow?')
    expect(screen.getByText('2.5313')).toBeInTheDocument()
  })

  it('shows the server\'s own value, not quantity times price', async () => {
    // ADR 0018 / [T-4] #24: value is a liquidation figure, never
    // quantity × price. Pick a price that gives a different, wrong answer
    // if the page ever starts computing it client-side: 5 shares at 0.6000
    // would be 3.0000, not the 2.5312 the server actually quotes.
    mockPortfolio(portfolio({ positions: [{ ...portfolio().positions[0], price: '0.6000' }] }))
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('2.5312')).toBeInTheDocument()
    expect(screen.queryByText('3.0000')).not.toBeInTheDocument()
  })

  it('shows unrealized P&L with a minus sign for a loss', async () => {
    mockPortfolio(portfolio())
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('-0.0001')).toBeInTheDocument()
  })

  it('shows unrealized P&L with a plus sign for a gain', async () => {
    mockPortfolio(portfolio({ positions: [{ ...portfolio().positions[0], unrealized_pnl: '1.5000' }] }))
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('+1.5000')).toBeInTheDocument()
  })

  it('shows unrealized P&L of exactly zero with no sign', async () => {
    // A fresh position's P&L is zero, not a gain (ADR 0018 / [T-4] #24).
    mockPortfolio(portfolio({ positions: [{ ...portfolio().positions[0], unrealized_pnl: '0.0000' }] }))
    mockMarket('mkt-1', market())
    renderPage()

    expect(await screen.findByText('0.0000')).toBeInTheDocument()
    expect(screen.queryByText('+0.0000')).not.toBeInTheDocument()
  })

  it('shows a settled position, whose price, value and P&L are null, as dashes instead of crashing', async () => {
    // [3.4] #12: a position in a settled market is no longer valued, and the
    // ledger sends null for these three. The rest of the page still renders.
    const settled = { ...portfolio().positions[0], price: null, value: null, unrealized_pnl: null }
    mockPortfolio(portfolio({ positions: [settled] }))
    mockMarket('mkt-1', market({ status: 'settled' }))
    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(3)
    expect(screen.queryByText('0.0000')).not.toBeInTheDocument()
  })

  it('shows what a winning settled position paid, not its old P&L', async () => {
    // [FE][3.4] #216: result/payout replace the P&L cell on a settled row,
    // never recomputed and never shown as zero.
    const settled = { ...portfolio().positions[0], price: null, value: null, unrealized_pnl: null, result: 'paid_out', payout: '5.0000' }
    mockPortfolio(portfolio({ positions: [settled] }))
    mockMarket('mkt-1', market({ status: 'settled' }))
    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.getByText('Paid out 5.0000')).toBeInTheDocument()
  })

  it('shows a losing settled position as worthless, not a zero P&L', async () => {
    const settled = { ...portfolio().positions[0], price: null, value: null, unrealized_pnl: null, result: 'worthless', payout: '0.0000' }
    mockPortfolio(portfolio({ positions: [settled] }))
    mockMarket('mkt-1', market({ status: 'settled' }))
    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(screen.getByText('Worthless')).toBeInTheDocument()
    expect(screen.queryByText('0.0000')).not.toBeInTheDocument()
  })

  it('does not add a settled position\'s payout into positions value or net worth', async () => {
    // ledger-service.md: the payout is already in balance, so positions_value
    // and net_worth are shown as the server sends them, never recomputed here.
    // balance already includes the 5.0000 payout; positions_value is zero
    // because the one position left is settled and no longer valued; net
    // worth is balance plus positions_value, same as the server would send.
    const settled = { ...portfolio().positions[0], price: null, value: null, unrealized_pnl: null, result: 'paid_out', payout: '5.0000' }
    mockPortfolio(portfolio({ balance: '1000.0000', positions_value: '0.0000', net_worth: '1000.0000', positions: [settled] }))
    mockMarket('mkt-1', market({ status: 'settled' }))
    renderPage()

    expect(await screen.findByText('0.0000 credits')).toBeInTheDocument()
    expect(screen.getAllByText('1,000.0000 credits')).toHaveLength(2)
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

  it('still shows cash, net worth and the other rows when one market fails to load', async () => {
    // Promise.all would fail the whole page here; Promise.allSettled must not.
    server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => HttpResponse.error()))
    mockMarket('mkt-2', market({ id: 'mkt-2', question: 'Will SMU win SUNIG?', outcomes: [{ id: 'o-win', position: 0, label: 'Yes' }] }))
    mockPortfolio(portfolio({
      positions: [
        portfolio().positions[0],
        { ...portfolio().positions[0], market_id: 'mkt-2', outcome_id: 'o-win', quantity: '2.0000' },
      ],
    }))
    renderPage()

    expect(await screen.findByText('997.4687 credits')).toBeInTheDocument()
    expect(screen.getByText('999.9999 credits')).toBeInTheDocument()
    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.getByText('Unknown market')).toBeInTheDocument()
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
