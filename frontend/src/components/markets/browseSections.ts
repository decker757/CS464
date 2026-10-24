import type { PriceState } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import type { MarketFigures } from '../../hooks/usePricesForMarkets'
import { compareCredits, subtractCredits } from '../../utils/formatCredits'
import { tradingStopped } from './marketStatus'

/** One named band above the full list, and the markets in it. */
export interface BrowseSection {
  /** Stable key, and what the section is: used for the heading's id too. */
  id: 'closing_soon' | 'trending' | 'movers'
  /** The heading a trader reads. */
  title: string
  /**
   * The rule, stated under the heading, so "why is this here" is never a
   * mystery (#448 AC 2).
   */
  rule: string
  markets: PublicMarketSummary[]
}

// How many markets a band holds. A band is a shortcut into the list, not a
// second copy of it: past about this many it stops being scannable and the
// full list below is the better tool.
const SECTION_SIZE = 3

// What counts as closing soon. Long enough to catch a market worth hurrying
// for, short enough that the band is not simply the top of a list already
// ordered by close time.
const CLOSING_SOON_HOURS = 24

/** Markets that can still be traded: a band of closed markets helps nobody. */
function stillTrading(markets: PublicMarketSummary[], now: Date): PublicMarketSummary[] {
  return markets.filter(market => !tradingStopped(market.status, market.close_time, now))
}

/** How far `market`'s leading outcome has moved over the last seven days, as
 *  an absolute decimal string; null when there is no change to measure. */
function weeklyMoveOf(snapshot: PriceState | undefined, figures: MarketFigures | undefined): string | null {
  if (!snapshot || !figures?.prices_7d_ago) return null

  // The first outcome is the one a card leads with, and on a binary book the
  // other is its mirror — so one outcome's move is the market's move.
  const [leading] = [...snapshot.prices].sort((a, b) => a.position - b.position)
  if (!leading) return null
  const before = figures.prices_7d_ago.find(price => price.outcome_id === leading.outcome_id)
  if (!before) return null

  const change = subtractCredits(leading.price, before.price)
  if (change === null) return null
  return change.startsWith('-') ? change.slice(1) : change
}

/**
 * The bands above the full browse list ([X-1] #448).
 *
 * A flat grid of fifty equally weighted cards has no entry point: a trader has
 * to read fifty questions to find one worth trading. These are three short
 * curated bands, each stating its own rule.
 *
 * **A band with nothing in it is left out, not rendered empty.** On a young
 * database most of them will be, which is the normal case here rather than an
 * edge case — so a band only exists once it has a market to show.
 *
 * Every market in a band is also still in the list below it. A band is a
 * shortcut into the list, not a partition of it: a trader who scrolls past
 * "Closing soon" must still find those markets where the ordering puts them.
 */
export function browseSectionsOf(
  markets: PublicMarketSummary[],
  snapshotsByMarket: Map<string, PriceState>,
  marketFigures: Map<string, MarketFigures>,
  now: Date,
): BrowseSection[] {
  const tradeable = stillTrading(markets, now)

  const closingSoon = tradeable
    .filter(market => {
      // Every published market has a close time, and `stillTrading` has
      // already dropped the ones whose has passed.
      const hoursLeft = (new Date(market.close_time).getTime() - now.getTime()) / 3_600_000
      return hoursLeft <= CLOSING_SOON_HOURS
    })
    // Soonest first, which is the one order this band is obviously about.
    .sort((left, right) => left.close_time.localeCompare(right.close_time))
    .slice(0, SECTION_SIZE)

  // Most traded first. A market nobody has traded is left out rather than
  // ranked last at zero: "trending" over untraded books is the confident name
  // on an arbitrary order that #447 exists to make honest.
  const trending = tradeable
    .filter(market => {
      const traded = marketFigures.get(market.id)?.credits_traded
      return traded !== undefined && compareCredits(traded, '0') > 0
    })
    .sort((left, right) => {
      const leftTraded = marketFigures.get(left.id)?.credits_traded ?? '0'
      const rightTraded = marketFigures.get(right.id)?.credits_traded ?? '0'
      return compareCredits(rightTraded, leftTraded)
    })
    .slice(0, SECTION_SIZE)

  // Biggest price change over the week, either direction: a market that fell
  // thirty points is every bit as interesting as one that rose.
  const moved: { market: PublicMarketSummary; move: string }[] = []
  for (const market of tradeable) {
    const move = weeklyMoveOf(snapshotsByMarket.get(market.id), marketFigures.get(market.id))
    if (move !== null && compareCredits(move, '0') > 0) moved.push({ market, move })
  }
  moved.sort((left, right) => compareCredits(right.move, left.move))
  const movers = moved.slice(0, SECTION_SIZE).map(entry => entry.market)

  const sections: BrowseSection[] = [
    {
      id: 'closing_soon',
      title: 'Closing soon',
      rule: `Trading stops within ${CLOSING_SOON_HOURS} hours`,
      markets: closingSoon,
    },
    { id: 'trending', title: 'Trending', rule: 'Most credits traded', markets: trending },
    { id: 'movers', title: 'Biggest movers', rule: 'Largest price change this week', markets: movers },
  ]
  return sections.filter(section => section.markets.length > 0)
}
