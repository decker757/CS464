import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PriceHistoryChart from './PriceHistoryChart'

const OUTCOMES = [
  { id: 'o-yes', label: 'Yes' },
  { id: 'o-no', label: 'No' },
]

// The per-outcome row logic that makes this "a chart over time" is
// toChartRows, tested directly in utils/priceHistoryChart.test.ts — the
// chart itself renders at zero width in tests, so a render test here could
// only ever check a recharts class name that shows up with no lines drawn.
describe('PriceHistoryChart', () => {
  it('shows an empty state when there is no price history', () => {
    render(<PriceHistoryChart outcomes={OUTCOMES} points={[]} />)
    expect(screen.getByText(/no price history yet/i)).toBeInTheDocument()
  })
})
