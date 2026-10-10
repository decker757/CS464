import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MarketTrade } from '../../api/ledgerApi'
import MarketTradeTable from './MarketTradeTable'

const TRADES: MarketTrade[] = [
  { id: 't1', created_at: '2026-01-01T12:00:00Z', username: 'alice', outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', average_price: '0.6000' },
  { id: 't2', created_at: '2026-01-02T12:00:00Z', username: 'bob', outcome_id: 'o-no', side: 'sell', quantity: '5.0000', average_price: '0.4500' },
]

const OUTCOME_LABELS = new Map([['o-yes', 'Yes'], ['o-no', 'No']])

describe('MarketTradeTable', () => {
  it('shows every trade with its user, outcome, side, quantity and price', () => {
    render(<MarketTradeTable trades={TRADES} outcomeLabels={OUTCOME_LABELS} />)

    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
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
