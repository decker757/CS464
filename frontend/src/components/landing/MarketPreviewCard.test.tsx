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

  it('writes the price as a percentage, the way the rest of the app does', () => {
    render(<MarketPreviewCard />)

    // The current price and the YES row, both from the one price constant.
    expect(screen.getAllByText('64.0%')).toHaveLength(2)
    expect(screen.queryAllByText(/[$¢]/)).toHaveLength(0)
  })
})
