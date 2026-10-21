import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { MarketTrade } from '../../api/ledgerApi'
import MarketTradeTable from './MarketTradeTable'

const TRADES: MarketTrade[] = [
  { transaction_id: 't1', created_at: '2026-01-01T12:00:00Z', user_id: 'aaaaaaaa-1111-2222-3333-444444444444', outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', average_price: '0.6000', total: '6.0000', state_version: 1 },
  { transaction_id: 't2', created_at: '2026-01-02T12:00:00Z', user_id: 'bbbbbbbb-1111-2222-3333-444444444444', outcome_id: 'o-no', side: 'sell', quantity: '5.0000', average_price: '0.4500', total: '2.2500', state_version: 2 },
]

const OUTCOME_LABELS = new Map([['o-yes', 'Yes'], ['o-no', 'No']])

const USERNAMES = new Map([
  ['aaaaaaaa-1111-2222-3333-444444444444', 'alice'],
  ['bbbbbbbb-1111-2222-3333-444444444444', 'bob'],
])

function renderTable(trades: MarketTrade[], outcomeLabels = OUTCOME_LABELS, usernames = USERNAMES) {
  // The User column links to each trader's history page, so the table needs
  // a router around it.
  return render(
    <MemoryRouter>
      <MarketTradeTable trades={trades} outcomeLabels={outcomeLabels} usernames={usernames} />
    </MemoryRouter>,
  )
}

describe('MarketTradeTable', () => {
  it('shows every trade with its date, user, outcome, side, quantity and price', () => {
    renderTable(TRADES)

    // A real date, read from created_at: a wrong field name renders
    // "Invalid Date". en-SG is day/month/year, and noon UTC on 1 January is
    // still 1 January in every timezone from UTC-11 to UTC+11.
    const [, firstTrade] = screen.getAllByRole('row')
    expect(within(firstTrade).getAllByRole('cell')[0]).toHaveTextContent(/^0?1\/0?1\/2026, /)

    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('No')).toBeInTheDocument()
    expect(screen.getByText('Buy')).toBeInTheDocument()
    expect(screen.getByText('Sell')).toBeInTheDocument()
    expect(screen.getByText('10.0000')).toBeInTheDocument()
    expect(screen.getByText('0.6000')).toBeInTheDocument()
  })

  it('links each trader to their own history page', () => {
    // [2.4] #434: the screen for spotting manipulation should not make an
    // admin copy a hex fragment into a search on another tab.
    renderTable(TRADES)

    expect(screen.getByRole('link', { name: 'alice' }))
      .toHaveAttribute('href', '/admin/users/aaaaaaaa-1111-2222-3333-444444444444')
  })

  it('falls back to the short id for a trader the lookup did not name, and still links', () => {
    // An id auth left out of the answer, or never named because the lookup
    // failed, shows as it did before the names arrived — never a blank cell.
    // The page it links to is addressed by the id, not by the name, so the
    // link works either way.
    renderTable(TRADES, OUTCOME_LABELS, new Map([['aaaaaaaa-1111-2222-3333-444444444444', 'alice']]))

    expect(screen.getByRole('link', { name: 'bbbbbbbb' }))
      .toHaveAttribute('href', '/admin/users/bbbbbbbb-1111-2222-3333-444444444444')
  })

  it('falls back to the raw outcome id when the market\'s outcomes have not loaded', () => {
    renderTable(TRADES, new Map())
    expect(screen.getByText('o-yes')).toBeInTheDocument()
  })

  it('shows average price as credits per share, not a percentage', () => {
    // #247: the same format as the trading panel and the portfolio, not
    // formatPrice's percentage — average_price can pass 1.0000 on a skewed
    // book (ledger-service.md). '2' is deliberately not already in the
    // four-decimal finished format, so this fails if the formatter is
    // skipped.
    const skewed: MarketTrade[] = [
      { ...TRADES[0], transaction_id: 't3', average_price: '2' },
    ]
    renderTable(skewed)
    expect(screen.getByText('2.0000')).toBeInTheDocument()
  })
})
