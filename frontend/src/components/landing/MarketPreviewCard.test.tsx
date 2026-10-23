import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import MarketPreviewCard from './MarketPreviewCard'

describe('MarketPreviewCard', () => {
  // The card is a picture of a market. A control on it would be focusable and
  // would do nothing, which is the bug #366 is about.
  //
  // The role queries pass `hidden: true` because the buy row is aria-hidden,
  // and a role query defaults to skipping that whole subtree — a <button>
  // restored inside it would be invisible to this test while still taking
  // keyboard focus, which is worse than the bug. The last assertion is the
  // backstop: focusability is a property of the element, not of the role.
  it('exposes no interactive control', () => {
    const { container } = render(<MarketPreviewCard />)

    expect(screen.queryAllByRole('button', { hidden: true })).toHaveLength(0)
    expect(screen.queryAllByRole('link', { hidden: true })).toHaveLength(0)
    expect(container.querySelectorAll('button, a, input, select, textarea, [tabindex]')).toHaveLength(0)
  })

  it('tells the visitor it is an example', () => {
    render(<MarketPreviewCard />)

    expect(screen.getByText(/an example market/i)).toBeInTheDocument()
  })

  // The "Live" badge and the "sarah_lim bought 20 YES shares · just now" line.
  it('claims nothing is live and shows no trade feed', () => {
    render(<MarketPreviewCard />)

    expect(screen.queryByText(/live/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/bought/i)).not.toBeInTheDocument()
  })

  // The market page shows each side's price once, as a percentage over the
  // words "implied probability". The card used to repeat the YES price as a
  // headline figure beside a bar for each side, which the page has neither of
  // (#435).
  it('writes each side’s price once, the way the market page does', () => {
    render(<MarketPreviewCard />)

    expect(screen.getByText('64.0%')).toBeInTheDocument()
    expect(screen.getByText('36.0%')).toBeInTheDocument()
    expect(screen.getAllByText('implied probability')).toHaveLength(2)
    expect(screen.queryAllByText(/[$¢]/)).toHaveLength(0)
  })

  // The seven-day sparkline and "up from 52.0% a week ago" (#435). The market
  // page draws no chart and reports no trend, so a visitor shown one here is
  // shown a feature that is not there after signing up. The sparkline has no
  // text, so the drawing itself is what the second assertion looks for.
  it('draws no price history, which the market page does not have', () => {
    const { container } = render(<MarketPreviewCard />)

    expect(screen.queryByText(/trend|a week ago/i)).not.toBeInTheDocument()
    expect(container.querySelector('svg')).toBeNull()
  })

  // The trading panel has one buy button, named after the outcome chosen in
  // the toggle above it, with no price on it. The card used to draw a priced
  // button for each side (#435).
  it('draws one buy control, for the chosen side, with no price on it', () => {
    render(<MarketPreviewCard />)

    expect(screen.getByText('Buy YES')).toBeInTheDocument()
    expect(screen.queryByText(/buy no/i)).not.toBeInTheDocument()
  })
})
