import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { OutcomeExposure } from '../../api/ledgerApi'
import MarketExposureTable from './MarketExposureTable'

// shares_outstanding and max_payout are deliberately equal — a winning share
// pays exactly one credit — but kept to a different number per outcome so a
// mislabelled column still fails this test.
const OUTCOMES: OutcomeExposure[] = [
  { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', max_payout: '120.0000' },
  { outcome_id: 'o-no', position: 1, shares_outstanding: '45.0000', max_payout: '45.0000' },
]

const OUTCOME_LABELS = new Map([['o-yes', 'Yes'], ['o-no', 'No']])

describe('MarketExposureTable', () => {
  it('shows shares outstanding and max payout for every outcome', () => {
    render(<MarketExposureTable outcomes={OUTCOMES} outcomeLabels={OUTCOME_LABELS} />)

    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('No')).toBeInTheDocument()
    expect(screen.getAllByText('120.0000')).toHaveLength(2) // shares outstanding and max payout, same figure
    expect(screen.getAllByText('45.0000')).toHaveLength(2)
  })

  it('falls back to the raw outcome id when the market\'s outcomes have not loaded', () => {
    render(<MarketExposureTable outcomes={OUTCOMES} outcomeLabels={new Map()} />)
    expect(screen.getByText('o-yes')).toBeInTheDocument()
  })
})
