import { describe, expect, it } from 'vitest'
import { DEMO_MARKETS, type DemoMarket } from './demoMarkets'

// The browse list puts the markets still trading first, soonest close first
// (market_service's `_browse_order`), so whichever open markets close soonest
// are the first screen a visitor sees. The seed gives that place to its
// liveliest markets on purpose (Ernest's pre-demo list, R9), rather than to
// whichever literals happen to be smallest.

type OpenDemoMarket = Extract<DemoMarket, { endsAs: 'open' }>

const openMarkets = DEMO_MARKETS.filter((market): market is OpenDemoMarket => market.endsAs === 'open')

// An outcome's price after the market's trades, worked out here rather than
// read from the app: every demo market is priced at b = 100 (the e2e helpers
// pin it), and LMSR quotes outcome i at e^(q_i / b) / Σ e^(q_j / b).
function priceOf(market: OpenDemoMarket, outcome: string): number {
  const sharesByOutcome = new Map(market.outcomes.map(label => [label, 0]))
  for (const trade of market.trades) {
    sharesByOutcome.set(trade.outcome, (sharesByOutcome.get(trade.outcome) ?? 0) + Number(trade.quantity))
  }
  let total = 0
  for (const shares of sharesByOutcome.values()) total += Math.exp(shares / 100)
  return Math.exp((sharesByOutcome.get(outcome) ?? 0) / 100) / total
}

function distanceFromEven(market: OpenDemoMarket): number {
  return Math.abs(priceOf(market, 'Yes') - 0.5)
}

describe('the open demo markets', () => {
  it('close the four headline markets before any other open market, so the browse list shows them first', () => {
    const headlines = openMarkets.filter(market => market.headline)
    const others = openMarkets.filter(market => !market.headline)

    expect(headlines).toHaveLength(4)
    const latestHeadline = Math.max(...headlines.map(market => market.closesInDays))
    const soonestOther = Math.min(...others.map(market => market.closesInDays))
    expect(latestHeadline).toBeLessThan(soonestOther)
  })

  it('make the headlines the Yes/No markets furthest from 50/50, and the five-way market', () => {
    const yesNo = openMarkets.filter(market => market.outcomes.length === 2)
    const yesNoHeadlines = yesNo.filter(market => market.headline)
    // Math.min of nothing is Infinity, which beats every other market.
    expect(yesNoHeadlines).toHaveLength(3)
    const leastLivelyHeadline = Math.min(...yesNoHeadlines.map(distanceFromEven))
    const liveliestOther = Math.max(...yesNo.filter(market => !market.headline).map(distanceFromEven))
    expect(leastLivelyHeadline).toBeGreaterThan(liveliestOther)

    const multiOutcome = openMarkets.filter(market => market.outcomes.length > 2)
    expect(multiOutcome.map(market => market.headline)).toEqual([true])
  })

  // README, "A demo database": the open markets close three weeks or more
  // after seeding, so a seed run before the showcase is still open on the day.
  it('all close at least three weeks after seeding', () => {
    const soonest = Math.min(...openMarkets.map(market => market.closesInDays))

    expect(soonest).toBeGreaterThanOrEqual(21)
  })
})

// #570: a buy limit at or above the price fills on arrival, and the seed stops
// on one. Far below it, no trade a presenter would make live reaches it, and
// the order never gets to fill in front of anyone (ADR 0024).
describe('the resting limit orders', () => {
  // Each mistake here would otherwise surface minutes into a one-shot run:
  // limit orders are refused on a market with more than two outcomes
  // (`409 market_not_binary`), and the seed looks the outcome up by label.
  it('are on two-outcome markets, and name one of their outcomes', () => {
    const withOrders = openMarkets.filter(market => market.restingOrder)
    expect(withOrders.length).toBeGreaterThan(0)

    for (const market of withOrders) {
      expect(market.outcomes).toHaveLength(2)
      expect(market.outcomes).toContain(market.restingOrder?.outcome)
    }
  })

  it('sit below their outcome\'s seeded price, and within ten points of it', () => {
    const withOrders = openMarkets.filter(market => market.restingOrder)
    expect(withOrders.length).toBeGreaterThan(0)

    for (const market of withOrders) {
      // Only narrows the type: the filter above has already kept these.
      if (!market.restingOrder) continue
      const seeded = priceOf(market, market.restingOrder.outcome)
      const limit = Number(market.restingOrder.limitPrice)
      expect(limit).toBeLessThan(seeded)
      expect(seeded - limit).toBeLessThanOrEqual(0.1)
    }
  })
})
