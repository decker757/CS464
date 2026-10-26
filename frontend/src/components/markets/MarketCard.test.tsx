import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PriceState } from '../../api/ledgerApi'
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

// Everything the card is told about its prices, which is nothing at all
// unless a test says otherwise: no snapshot and no failure is a read still on
// its way.
interface PriceProps {
  snapshot?: PriceState
  priceReadFailed?: boolean
  onRetryPrice?: () => void
}

function renderCard(market: PublicMarketSummary, prices: PriceProps = {}) {
  render(
    <MemoryRouter>
      <MarketCard market={market} {...prices} />
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

// [X-1] #431 AC 1 and its first Definition of Done item. Both states were one
// dash, and usePricesForMarkets' own comment said so on purpose, so a trader
// had no way to tell whether to wait or to reload. The detail page already
// said "loading price…" for the first of the two.
describe('a card with no prices', () => {
  it('says the prices are still coming while the read is out', () => {
    renderCard(MARKET)

    expect(screen.getByText('Loading prices…')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('says the prices could not be read once the read has failed, and offers to read them again', async () => {
    const actor = userEvent.setup()
    const retry = vi.fn()
    renderCard(MARKET, { priceReadFailed: true, onRetryPrice: retry })

    expect(screen.getByRole('alert')).toHaveTextContent('Prices could not be loaded.')
    expect(screen.queryByText('Loading prices…')).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: 'Retry' }))

    expect(retry).toHaveBeenCalledTimes(1)
  })

  // The Retry control sits outside the card's link for this reason: inside it,
  // a click would open the market as well as ask for the price again, and the
  // trader would never see the answer.
  it('does not open the market when Retry is clicked', async () => {
    const actor = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/markets']}>
        <Routes>
          <Route path="/markets" element={<MarketCard market={MARKET} priceReadFailed onRetryPrice={() => {}} />} />
          <Route path="/markets/:id" element={<p>market detail</p>} />
        </Routes>
      </MemoryRouter>,
    )

    await actor.click(screen.getByRole('button', { name: 'Retry' }))

    expect(screen.queryByText('market detail')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })
})

// [X-1] #431 AC 3: "Pending" is a step of a state machine that exists for a
// rule a trader knows nothing about (ADR 0016), and an approved result can
// still be sent back (ADR 0023), so the trader's word for it is not "final".
// The admin list keeps STATUS_CONFIG's own words — AdminMarketsPage's test
// reads its "Pending" tab by that name.
it("names a market being resolved in the trader's words", () => {
  renderCard({ ...MARKET, status: 'pending_resolution' })

  expect(screen.getByText('Resolving')).toBeInTheDocument()
  expect(screen.queryByText('Pending')).not.toBeInTheDocument()
})

// #526, and market-service.md's void-screen notes (7): a voided market has no
// result, so a probability on its card would read as a market still priced.
// The batch snapshot goes on carrying its last prices, so the card must
// ignore them rather than wait for them to stop arriving.
it('shows a voided market as Voided, with no price even though its snapshot carries prices', () => {
  const snapshot: PriceState = {
    market_id: 'm1',
    state_version: 7,
    prices: [
      { outcome_id: 'm1-0', position: 0, price: '0.6200' },
      { outcome_id: 'm1-1', position: 1, price: '0.3800' },
    ],
    occurred_at: '2026-10-18T11:00:00Z',
  }
  renderCard({ ...MARKET, status: 'voided', close_time: '2026-10-10T00:00:00Z' }, { snapshot })

  expect(screen.getByText('Voided')).toBeInTheDocument()
  expect(screen.queryByLabelText('Yes price')).not.toBeInTheDocument()
  expect(screen.queryByText('62.0%')).not.toBeInTheDocument()
  expect(screen.queryByText(/last trade/i)).not.toBeInTheDocument()
})

// A voided card shows no prices at all, so a failed read of them is nothing
// to retry: offering Retry for a price the card will never draw would send
// the trader after a number that does not exist.
it('offers no Retry and no price error on a voided card whose price read failed', () => {
  renderCard({ ...MARKET, status: 'voided', close_time: '2026-10-10T00:00:00Z' }, { priceReadFailed: true, onRetryPrice: () => {} })

  expect(screen.getByText('Voided')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByText(/loading prices/i)).not.toBeInTheDocument()
})
