import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PublicMarketSummary } from '../../api/marketApi'
import MarketCard from './MarketCard'

const NOW = new Date('2026-10-18T12:00:00Z')

const MARKET: PublicMarketSummary = {
  id: 'm1',
  status: 'open',
  question: 'Will SMU win SUNIG?',
  close_time: '2026-10-18T15:12:00Z',
  outcomes: [
    { id: 'm1-0', position: 0, label: 'Yes' },
    { id: 'm1-1', position: 1, label: 'No' },
  ],
}

function renderCard(market: PublicMarketSummary) {
  render(
    <MemoryRouter>
      <MarketCard market={market} />
    </MemoryRouter>,
  )
}

describe('MarketCard', () => {
  afterEach(() => vi.useRealTimers())

  // [X-1] #364 AC 2: "the value updates rather than freezing at first render".
  // A countdown computed once is worse than the date it replaced — it goes on
  // offering trading time after the market has stopped accepting trades.
  it('counts the close down as time passes', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    renderCard(MARKET)
    expect(screen.getByText('Closes in 3h 12m')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(60_000))

    expect(screen.getByText('Closes in 3h 11m')).toBeInTheDocument()
  })

  // ADR 0011: trading stops on the clock, and the status column catches up a
  // sweep interval later. The card must not wait for it.
  it('reads Closed once the close time passes, while the status still says open', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    renderCard({ ...MARKET, close_time: new Date(NOW.getTime() + 30_000).toISOString() })
    expect(screen.getByText('Closes in under a minute')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(60_000))

    expect(screen.getByText('Closed')).toBeInTheDocument()
  })
})
