import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { OutcomeExposure } from '../../api/ledgerApi'
import MarketExposureTable from './MarketExposureTable'

const OUTCOMES: OutcomeExposure[] = [
  { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', payout_if_wins: '130.0000', residue_if_wins: '-20.0000' },
  { outcome_id: 'o-no', position: 1, shares_outstanding: '45.0000', payout_if_wins: '50.0000', residue_if_wins: '55.0000' },
]

const OUTCOME_LABELS = new Map([['o-yes', 'Yes'], ['o-no', 'No']])

describe('MarketExposureTable', () => {
  it('shows shares outstanding and payout if wins for every outcome', () => {
    render(<MarketExposureTable outcomes={OUTCOMES} outcomeLabels={OUTCOME_LABELS} />)

    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('No')).toBeInTheDocument()
    expect(screen.getByText('120.0000')).toBeInTheDocument()
    expect(screen.getByText('130.0000')).toBeInTheDocument()
    expect(screen.getByText('45.0000')).toBeInTheDocument()
    expect(screen.getByText('50.0000')).toBeInTheDocument()
  })

  it('says a shortfall is a top-up and a surplus is kept, rather than leaving it to a minus sign', () => {
    // #377: "-20.0000" and "55.0000" in one column differ by one narrow
    // glyph. The words are the worst-case line's (#374): "If Yes wins, the
    // platform tops up 400.0000." in MarketExposurePage.test.tsx.
    render(<MarketExposureTable outcomes={OUTCOMES} outcomeLabels={OUTCOME_LABELS} />)

    const yesRow = screen.getByRole('row', { name: /^Yes/ })
    const noRow = screen.getByRole('row', { name: /^No/ })
    expect(within(yesRow).getByText('Platform tops up 20.0000')).toBeInTheDocument()
    expect(within(noRow).getByText('Pool keeps 55.0000')).toBeInTheDocument()
  })

  it('shows a dash for a residue it cannot read, not words around one', () => {
    const unreadable: OutcomeExposure[] = [
      { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', payout_if_wins: '130.0000', residue_if_wins: '1E-4' },
    ]
    render(<MarketExposureTable outcomes={unreadable} outcomeLabels={OUTCOME_LABELS} />)

    const residueCell = within(screen.getByRole('row', { name: /^Yes/ })).getAllByRole('cell')[3]
    expect(residueCell).toHaveTextContent(/^—$/)
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
