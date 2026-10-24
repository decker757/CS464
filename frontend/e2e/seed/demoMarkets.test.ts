import { describe, expect, it } from 'vitest'
import { DEMO_MARKETS, type DemoMarket } from './demoMarkets'

// The browse list puts the markets still trading first, soonest close first
// (market_service's `_browse_order`), so whichever open markets close soonest
// are the first screen a visitor sees. The seed gives that place to its
// liveliest markets on purpose (Ernest's pre-demo list, R9), rather than to
// whichever literals happen to be smallest.

type OpenDemoMarket = Extract<DemoMarket, { endsAs: 'open' }>

const openMarkets = DEMO_MARKETS.filter((market): market is OpenDemoMarket => market.endsAs === 'open')

// The Yes price after the market's trades, worked out here rather than read
// from the app: every demo market is priced at b = 100 (the e2e helpers pin
// it), and a two-outcome LMSR quotes Yes at 1 / (1 + e^((qNo - qYes) / b)).
function yesPrice(market: OpenDemoMarket): number {
  let yesShares = 0
  let noShares = 0
  for (const trade of market.trades) {
    if (trade.outcome === 'Yes') yesShares += Number(trade.quantity)
    else noShares += Number(trade.quantity)
  }
  return 1 / (1 + Math.exp((noShares - yesShares) / 100))
}

function distanceFromEven(market: OpenDemoMarket): number {
  return Math.abs(yesPrice(market) - 0.5)
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
