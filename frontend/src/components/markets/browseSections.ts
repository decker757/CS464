import type { PriceState } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import type { MarketFigures } from '../../hooks/usePricesForMarkets'
import { compareCredits, subtractCredits, unsignedCredits } from '../../utils/formatCredits'
import { tradingStopped } from './marketStatus'

/** The page's single featured market, and why it was chosen. */
export interface FeaturedMarket {
  market: PublicMarketSummary
  /** Stated beside it, so a featured slot is never an unexplained promotion. */
  rule: string
}

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

// What counts as closing soon: long enough to catch a market worth hurrying
// for, short enough that the band means "today" rather than "eventually".
const CLOSING_SOON_HOURS = 24

/** Markets that can still be traded: a band of closed markets helps nobody. */
function stillTrading(markets: PublicMarketSummary[], now: Date): PublicMarketSummary[] {
  return markets.filter(market => !tradingStopped(market.status, market.close_time, now))
}

/** How far `market` has moved over the last seven days — the largest move of
 *  any of its outcomes, as an absolute decimal string; null when there is no
 *  change to measure. */
function weeklyMoveOf(snapshot: PriceState | undefined, figures: MarketFigures | undefined): string | null {
  if (!snapshot || !figures?.prices_7d_ago) return null

  // Every outcome, not the first: only on a binary book is the second the
  // first one's mirror. On three outcomes the first can hold still while the
  // other two trade places, and that market has moved.
  let largest: string | null = null
  for (const price of snapshot.prices) {
    const before = figures.prices_7d_ago.find(old => old.outcome_id === price.outcome_id)
    if (!before) continue
    const change = subtractCredits(price.price, before.price)
    if (change === null) continue
    const move = unsignedCredits(change)
    if (largest === null || compareCredits(move, largest) > 0) largest = move
  }
  return largest
}

/** The markets that have traded at all, the most credits traded first. The
 *  hero and Trending rank the same way, so they are one ranking: the hero is
 *  its first market, and Trending the next three. */
function mostTradedFirst(
  markets: PublicMarketSummary[],
  marketFigures: Map<string, MarketFigures>,
): PublicMarketSummary[] {
  const tradedOf = (market: PublicMarketSummary) => marketFigures.get(market.id)?.credits_traded ?? '0'
  return markets
    .filter(market => compareCredits(tradedOf(market), '0') > 0)
    .sort((left, right) => compareCredits(tradedOf(right), tradedOf(left)))
}

/**
 * The one market at the top of the page, or null when nothing earns the slot
 * ([X-1] #448).
 *
 * The busiest market the page has loaded. Volume is the only honest basis
 * available: it is the one figure that says other people are here, which is
 * what a featured slot is for. A market nobody has traded is never featured —
 * promoting an untouched book is how the screen came to read as dead in the
 * first place — so on a database with no trades there is simply no hero, which
 * is the same rule every band follows.
 */
export function featuredMarketOf(
  markets: PublicMarketSummary[],
  marketFigures: Map<string, MarketFigures>,
  now: Date,
): FeaturedMarket | null {
  const [market] = mostTradedFirst(stillTrading(markets, now), marketFigures)
  if (!market) return null
  return { market, rule: 'Busiest market by credits traded' }
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
  /**
   * The market already shown as the page's hero, left out of every band.
   *
   * It is the busiest market, so it would otherwise lead Trending as well and
   * appear twice above the list — which reads as a broken band rather than as
   * emphasis. It is still in the full list below, like every banded market.
   */
  featuredId?: string,
): BrowseSection[] {
  const tradeable = stillTrading(markets, now).filter(market => market.id !== featuredId)

  const closingSoon = tradeable
    .filter(market => {
      // Every published market has a close time, and `stillTrading` has
      // already dropped the ones whose has passed.
      const hoursLeft = (new Date(market.close_time).getTime() - now.getTime()) / 3_600_000
      return hoursLeft <= CLOSING_SOON_HOURS
    })
    // Soonest first, sorted here rather than taken from the list: the band's
    // rule is the order, and the list below need not stay ordered by close
    // time (#433). market-service.md's case against a browser re-sort is the
    // early-closed market, and `stillTrading` has dropped every one of those.
    // By the instant, not the text, which a written offset would misorder.
    .sort((left, right) => new Date(left.close_time).getTime() - new Date(right.close_time).getTime())
    .slice(0, SECTION_SIZE)

  // Most traded first. A market nobody has traded is left out rather than
  // ranked last at zero: "trending" over untraded books is the confident name
  // on an arbitrary order that #447 exists to make honest.
  const trending = mostTradedFirst(tradeable, marketFigures).slice(0, SECTION_SIZE)

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
