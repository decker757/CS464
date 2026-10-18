import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { PricePoint } from '../../api/ledgerApi'
import PriceHistoryChart from './PriceHistoryChart'

const OUTCOMES = [
  { id: 'o-yes', label: 'Yes' },
  { id: 'o-no', label: 'No' },
]

function point(occurredAt: string, stateVersion: number): PricePoint {
  return {
    state_version: stateVersion,
    occurred_at: occurredAt,
    prices: [
      { outcome_id: 'o-yes', position: 0, price: '0.6000' },
      { outcome_id: 'o-no', position: 1, price: '0.4000' },
    ],
  }
}

// The lines themselves are inside recharts, which draws nothing at the zero
// width a jsdom container reports — the per-outcome row logic behind them is
// toChartRows, tested directly in utils/priceHistoryChart.test.ts. What is
// asserted here is what a browser-less reader still gets: the legend naming
// each line, the range control, and the window those two describe.
describe('PriceHistoryChart', () => {
  it('shows an empty state when there is no price history', () => {
    render(<PriceHistoryChart outcomes={OUTCOMES} points={[]} />)
    expect(screen.getByText(/no price history yet/i)).toBeInTheDocument()
  })

  it('names every outcome in a legend, so the lines are not told apart by colour alone', () => {
    render(<PriceHistoryChart outcomes={OUTCOMES} points={[point('2026-03-05T01:00:00Z', 0)]} />)

    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText('No')).toBeInTheDocument()
  })

  it('narrows the chart to the chosen time range, measured back from the last trade', async () => {
    const points = [
      point('2026-03-05T01:00:00Z', 0),
      point('2026-03-07T01:00:00Z', 1),
      point('2026-03-07T01:30:00Z', 2),
    ]
    const actor = userEvent.setup()
    render(<PriceHistoryChart outcomes={OUTCOMES} points={points} />)
    expect(screen.getByText(/showing all 3 price points/i)).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: 'Last hour' }))

    expect(screen.getByText(/showing 2 of 3 price points/i)).toBeInTheDocument()
  })

  it('writes the window as time of day within a single day', () => {
    const sameAfternoon = [point('2026-03-05T01:00:00Z', 0), point('2026-03-05T04:00:00Z', 1)]

    render(<PriceHistoryChart outcomes={OUTCOMES} points={sameAfternoon} />)

    // Local time, so the digits depend on the machine's zone; the format is
    // what follows from the span, and it is what #361 asks for.
    expect(screen.getByText(/showing all 2 price points/i)).toHaveTextContent(/\d{2}:\d{2} – \d{2}:\d{2}/)
  })

  it('writes the window as dates over a span longer than a day', () => {
    const acrossDays = [point('2026-03-05T01:00:00Z', 0), point('2026-03-09T01:00:00Z', 1)]

    render(<PriceHistoryChart outcomes={OUTCOMES} points={acrossDays} />)

    expect(screen.getByText(/showing all 2 price points/i)).toHaveTextContent(/\d{1,2} [A-Za-z]{3} – \d{1,2} [A-Za-z]{3}/)
  })
})
