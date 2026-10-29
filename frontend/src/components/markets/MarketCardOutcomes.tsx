import type { OutcomePrice, PriceState } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { usePriceMovements } from '../../hooks/usePriceMovements'
import { weeklyChangesOf } from '../../utils/weeklyChange'
import BinaryOutcomeSplit from './BinaryOutcomeSplit'
import OutcomeChips from './OutcomeChips'
import { pricedSidesOf } from './outcomeSides'

interface MarketCardOutcomesProps {
  /** The market's outcomes, in position order. */
  outcomes: PublicOutcome[]
  /** The market's price snapshot; undefined until it arrives, or if the read failed. */
  snapshot?: PriceState
  /** False for a voided market, which has no price to show (#526). */
  showsPrices: boolean
  /** True for a book nobody has traded, whose prices are only its seed (#445). */
  hasNeverTraded: boolean
  /** Each outcome's price a week ago, from the page's one batch read; null
   *  when the ledger has none, undefined until the batch has answered (#564). */
  pricesWeekAgo?: OutcomePrice[] | null
}

// A browse card's outcomes and their prices ([X-1] #441).
//
// The sides used to be equal grey chips at 50/50 and at 89/11 alike, so which
// way the crowd leans had to be read off two small numbers. A priced
// two-outcome market is now drawn as its two sides over one bar split at
// their prices. Everything else keeps a chip per outcome: more than two
// outcomes, under a thin split bar of their own, and any market with no price
// to split.
export default function MarketCardOutcomes({ outcomes, snapshot, showsPrices, hasNeverTraded, pricesWeekAgo }: MarketCardOutcomesProps) {
  // Which outcomes have just moved, and which way, for the cue on each price.
  const movements = usePriceMovements(snapshot)
  const hasPrices = showsPrices && !hasNeverTraded
  const sides = hasPrices && snapshot ? pricedSidesOf(outcomes, snapshot.prices) : null
  // Where each outcome has gone over the week, beside where it is (#564).
  // Measured from the live price, so a trade moves it with the price, while
  // the week-old end only moves when the batch is read again.
  const weeklyChanges = hasPrices ? weeklyChangesOf(snapshot, pricesWeekAgo) : null

  if (sides && sides.length === 2) {
    return <BinaryOutcomeSplit sides={sides} movements={movements} weeklyChanges={weeklyChanges} />
  }
  return (
    <OutcomeChips
      outcomes={outcomes}
      snapshot={snapshot}
      hasPrices={hasPrices}
      sides={sides}
      movements={movements}
      weeklyChanges={weeklyChanges}
    />
  )
}
