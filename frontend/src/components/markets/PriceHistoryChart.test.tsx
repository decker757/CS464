import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { PricePoint } from '../../api/ledgerApi'
import PriceHistoryChart from './PriceHistoryChart'

const OUTCOMES = [
  { id: 'o-yes', label: 'Yes' },
  { id: 'o-no', label: 'No' },
]

const POINTS: PricePoint[] = [
  { outcome_id: 'o-yes', position: 0, price: '0.6000', occurred_at: '2026-01-01T00:00:00Z' },
  { outcome_id: 'o-no', position: 1, price: '0.4000', occurred_at: '2026-01-01T00:00:00Z' },
  { outcome_id: 'o-yes', position: 0, price: '0.7000', occurred_at: '2026-01-02T00:00:00Z' },
  { outcome_id: 'o-no', position: 1, price: '0.3000', occurred_at: '2026-01-02T00:00:00Z' },
]

describe('PriceHistoryChart', () => {
  it('shows an empty state when there is no price history', () => {
    render(<PriceHistoryChart outcomes={OUTCOMES} points={[]} />)
    expect(screen.getByText(/no price history yet/i)).toBeInTheDocument()
  })

  it('renders a chart when there is price history', () => {
    const { container } = render(<PriceHistoryChart outcomes={OUTCOMES} points={POINTS} />)
    expect(container.querySelector('.recharts-responsive-container')).toBeInTheDocument()
  })
})
