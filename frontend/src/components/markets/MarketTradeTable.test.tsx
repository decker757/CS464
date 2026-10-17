import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MarketTrade } from '../../api/ledgerApi'
import MarketTradeTable from './MarketTradeTable'

const TRADES: MarketTrade[] = [
  { transaction_id: 't1', created_at: '2026-01-01T12:00:00Z', user_id: 'aaaaaaaa-1111-2222-3333-444444444444', outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', average_price: '0.6000', total: '6.0000', state_version: 1 },
  { transaction_id: 't2', created_at: '2026-01-02T12:00:00Z', user_id: 'bbbbbbbb-1111-2222-3333-444444444444', outcome_id: 'o-no', side: 'sell', quantity: '5.0000', average_price: '0.4500', total: '2.2500', state_version: 2 },
]

const OUTCOME_LABELS = new Map([['o-yes', 'Yes'], ['o-no', 'No']])

describe('MarketTradeTable', () => {
  it('shows every trade with its date, user, outcome, side, quantity and price', () => {
    render(<MarketTradeTable trades={TRADES} outcomeLabels={OUTCOME_LABELS} />)

    // A real date, read from created_at: a wrong field name renders
    // "Invalid Date". en-SG is day/month/year, and noon UTC on 1 January is
    // still 1 January in every timezone from UTC-11 to UTC+11.
    const [, firstTrade] = screen.getAllByRole('row')
    expect(within(firstTrade).getAllByRole('cell')[0]).toHaveTextContent(/^0?1\/0?1\/2026, /)

    expect(screen.getByText('aaaaaaaa')).toBeInTheDocument()
    expect(screen.getByText('bbbbbbbb')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('No')).toBeInTheDocument()
    expect(screen.getByText('Buy')).toBeInTheDocument()
    expect(screen.getByText('Sell')).toBeInTheDocument()
    expect(screen.getByText('10.0000')).toBeInTheDocument()
    expect(screen.getByText('0.6000')).toBeInTheDocument()
  })

  it('falls back to the raw outcome id when the market\'s outcomes have not loaded', () => {
    render(<MarketTradeTable trades={TRADES} outcomeLabels={new Map()} />)
    expect(screen.getByText('o-yes')).toBeInTheDocument()
  })
})
