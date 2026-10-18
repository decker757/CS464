import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import MarketPreviewCard from './MarketPreviewCard'

describe('MarketPreviewCard', () => {
  // The card is a picture of a market. A control on it would be focusable and
  // would do nothing, which is the bug #366 is about.
  it('exposes no interactive control', () => {
    render(<MarketPreviewCard />)

    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryAllByRole('link')).toHaveLength(0)
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

    expect(screen.getAllByText('64.0%')[0]).toBeInTheDocument()
    expect(screen.queryAllByText(/[$¢]/)).toHaveLength(0)
  })
})
