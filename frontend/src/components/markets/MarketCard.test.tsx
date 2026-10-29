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
  const view = render(
    <MemoryRouter>
      <MarketCard market={market} {...prices} />
    </MemoryRouter>,
  )
  // Re-renders with a new snapshot, the way the list does when a price frame
  // arrives on the socket ([X-1] #444).
  return {
    repriceWith(next: PriceState) {
      view.rerender(
        <MemoryRouter>
          <MarketCard market={market} {...prices} snapshot={next} />
        </MemoryRouter>,
      )
    },
  }
}

// A snapshot pricing this market's two outcomes, at a given book version.
function pricedAt(version: number, yes: string, no: string): PriceState {
  return {
    market_id: 'm1',
    state_version: version,
    prices: [
      { outcome_id: 'm1-0', position: 0, price: yes },
      { outcome_id: 'm1-1', position: 1, price: no },
    ],
    occurred_at: '2026-10-18T11:59:00Z',
  }
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

// [X-1] #444 AC 3: a price that changes is visibly marked as having changed,
// and the cue is not colour alone. The arrow is the half that survives a
// trader who cannot tell the two tints apart, and the accessible name is the
// half that survives not seeing the card at all.
describe('a price that has just moved', () => {
  afterEach(() => vi.useRealTimers())

  it('marks the outcome that rose, in words and not only in colour', () => {
    const { repriceWith } = renderCard(MARKET, { snapshot: pricedAt(1, '0.5000', '0.5000') })

    act(() => repriceWith(pricedAt(2, '0.6200', '0.3800')))

    // Said in words, for a screen reader and for the colour-blind case.
    expect(screen.getByLabelText('Yes price rose')).toHaveTextContent('62.0%')
    expect(screen.getByLabelText('No price fell')).toHaveTextContent('38.0%')
    // And drawn as a shape, not only as a tint.
    expect(screen.getByLabelText('Yes price rose')).toHaveTextContent('▲')
    expect(screen.getByLabelText('No price fell')).toHaveTextContent('▼')
  })

  // Every card would flash on load otherwise, which says "everything just
  // changed" on a page where nothing has.
  it('does not mark the first price it is given', () => {
    renderCard(MARKET, { snapshot: pricedAt(1, '0.5000', '0.5000') })

    expect(screen.getByLabelText('Yes price')).toBeInTheDocument()
    expect(screen.queryByLabelText('Yes price rose')).not.toBeInTheDocument()
  })

  // A repaint that does not move the number is not an event. The list re-reads
  // every still-trading market twice a minute, so most repaints are this.
  it('does not mark a price that was re-read unchanged', () => {
    const { repriceWith } = renderCard(MARKET, { snapshot: pricedAt(1, '0.5000', '0.5000') })

    act(() => repriceWith(pricedAt(2, '0.5000', '0.5000')))

    expect(screen.getByLabelText('Yes price')).toBeInTheDocument()
    expect(screen.queryByLabelText(/rose|fell/)).not.toBeInTheDocument()
  })

  // The cue is temporary: two trades in quick succession have to read as two
  // events, not as one that never ends.
  it('stops marking the price after a moment', () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { repriceWith } = renderCard(MARKET, { snapshot: pricedAt(1, '0.5000', '0.5000') })

    act(() => repriceWith(pricedAt(2, '0.6200', '0.3800')))
    expect(screen.getByLabelText('Yes price rose')).toBeInTheDocument()

    act(() => { vi.advanceTimersByTime(2_000) })

    expect(screen.getByLabelText('Yes price')).toBeInTheDocument()
    expect(screen.queryByLabelText('Yes price rose')).not.toBeInTheDocument()
  })
})

// [X-1] #441. The sides used to be two equal chips at 50/50 and at 89/11
// alike, so the one fact a card exists to say — which way the crowd leans,
// and how far — had to be read rather than seen. A priced card now draws a
// bar split at the prices, named for what it shows.
describe('the split bar on a priced card', () => {
  // The bar's segments, in outcome order. jsdom lays nothing out, so the
  // share each segment is given is the only place its width exists here;
  // e2e/browse-prices measures the widths a browser actually draws from it.
  function segmentsOf(bar: HTMLElement): Element[] {
    return Array.from(bar.children)
  }

  it('splits at the prices, and says so', () => {
    const { repriceWith } = renderCard(MARKET, { snapshot: pricedAt(3, '0.8900', '0.1100') })

    const bar = screen.getByRole('img', { name: 'Yes 89.0%, No 11.0%' })
    expect(segmentsOf(bar)[0]).toHaveStyle({ flexGrow: '0.89' })
    expect(segmentsOf(bar)[1]).toHaveStyle({ flexGrow: '0.11' })

    // And the other way round once the crowd turns: the first side is not
    // simply the wider one.
    act(() => repriceWith(pricedAt(4, '0.3000', '0.7000')))

    const turned = screen.getByRole('img', { name: 'Yes 30.0%, No 70.0%' })
    expect(segmentsOf(turned)[0]).toHaveStyle({ flexGrow: '0.3' })
    expect(segmentsOf(turned)[1]).toHaveStyle({ flexGrow: '0.7' })
  })

  // AC 5 and #431: a card with no price must not draw a split at all. The
  // traps are the two cards whose snapshot *does* carry prices — an untraded
  // book quotes its 50/50 seed, which is nobody's opinion (#445), and a voided
  // market's last prices answer a question with no answer (#526) — and a card
  // still waiting, which has nothing to divide by.
  it.each([
    { state: 'untraded', market: MARKET, prices: { snapshot: pricedAt(0, '0.5000', '0.5000') } },
    { state: 'voided', market: { ...MARKET, status: 'voided' as const }, prices: { snapshot: pricedAt(7, '0.6200', '0.3800') } },
    { state: 'still loading', market: MARKET, prices: {} },
  ])('draws no split for a market that is $state', ({ market, prices }) => {
    renderCard(market, prices)

    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  // AC 4: five sides in one row would be five slivers of truncated text. A
  // market with more outcomes keeps one readable chip each, under one bar.
  it('draws a market with more than two outcomes as one bar, each outcome still named and priced', () => {
    const fiveWay: PublicMarketSummary = {
      ...MARKET,
      outcomes: ['Red', 'Blue', 'Green', 'Gold', 'Grey'].map((label, position) => ({ id: `m1-${position}`, position, label })),
    }
    const prices = ['0.4000', '0.3000', '0.1500', '0.1000', '0.0500']
    renderCard(fiveWay, {
      snapshot: {
        market_id: 'm1',
        state_version: 9,
        prices: prices.map((price, position) => ({ outcome_id: `m1-${position}`, position, price })),
        occurred_at: '2026-10-18T11:59:00Z',
      },
    })

    expect(screen.getByLabelText('Red price')).toHaveTextContent('40.0%')
    expect(screen.getByLabelText('Grey price')).toHaveTextContent('5.0%')
    const bar = screen.getByRole('img', { name: 'Red 40.0%, Blue 30.0%, Green 15.0%, Gold 10.0%, Grey 5.0%' })
    expect(segmentsOf(bar)[0]).toHaveStyle({ flexGrow: '0.4' })
    expect(segmentsOf(bar)[4]).toHaveStyle({ flexGrow: '0.05' })
  })
})
