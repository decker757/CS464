import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { OutcomeExposure } from '../../api/ledgerApi'
import MarketExposureTable from './MarketExposureTable'

const OUTCOMES: OutcomeExposure[] = [
  { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', payout_if_wins: '130.0000', residue_if_wins: '-20.0000' },
  { outcome_id: 'o-no', position: 1, shares_outstanding: '45.0000', payout_if_wins: '50.0000', residue_if_wins: '55.0000' },
]

const OUTCOME_LABELS = new Map([['o-yes', 'Yes'], ['o-no', 'No']])

describe('MarketExposureTable', () => {
  it('shows shares outstanding, payout if wins, and pool residue for every outcome', () => {
    render(<MarketExposureTable outcomes={OUTCOMES} outcomeLabels={OUTCOME_LABELS} />)

    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('No')).toBeInTheDocument()
    expect(screen.getByText('120.0000')).toBeInTheDocument()
    expect(screen.getByText('130.0000')).toBeInTheDocument()
    expect(screen.getByText('-20.0000')).toBeInTheDocument()
    expect(screen.getByText('45.0000')).toBeInTheDocument()
    expect(screen.getByText('50.0000')).toBeInTheDocument()
    expect(screen.getByText('55.0000')).toBeInTheDocument()
  })

  it('falls back to the raw outcome id when the market\'s outcomes have not loaded', () => {
    render(<MarketExposureTable outcomes={OUTCOMES} outcomeLabels={new Map()} />)
    expect(screen.getByText('o-yes')).toBeInTheDocument()
  })

  it('shows a dash for payout and residue once the market is settled', () => {
    const settled: OutcomeExposure[] = [
      { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', payout_if_wins: null, residue_if_wins: null },
    ]
    render(<MarketExposureTable outcomes={settled} outcomeLabels={OUTCOME_LABELS} />)
    expect(screen.getAllByText('—')).toHaveLength(2)
  })
})
