import type { PriceMovement } from './priceMovement'
import OutcomePriceText from './OutcomePriceText'
import { SIDE_TONE_TEXT, type PricedSide } from './outcomeSides'
import PriceSplitBar from './PriceSplitBar'
import WeeklyChangeText from './WeeklyChangeText'

interface BinaryOutcomeSplitProps {
  /** The market's two outcomes, priced. */
  sides: PricedSide[]
  movements: Map<string, PriceMovement>
  /** Each outcome's change over the week, by id; null when there is none (#564). */
  weeklyChanges: Map<string, string> | null
}

// A priced two-outcome market on a browse card ([X-1] #441): each side's label
// and price at its own end of one row, coloured by side, and under them a
// thick bar split at the prices. On a binary book the two prices are
// complements, so one bar says what two would.
export default function BinaryOutcomeSplit({ sides, movements, weeklyChanges }: BinaryOutcomeSplitProps) {
  return (
    <div className="mt-4">
      <div className="mb-2 flex gap-3">
        {sides.map((side, index) => (
          <div
            key={side.outcome.id}
            className={`flex min-w-0 flex-1 flex-col ${index === 1 ? 'items-end' : 'items-start'} ${SIDE_TONE_TEXT[side.tone]}`}
          >
            {/* min-w-0 and truncate: a label can be one unbroken word, wider
                than the page (#516). The price never shrinks. */}
            <div className="flex max-w-full min-w-0 items-baseline gap-1.5">
              <span title={side.outcome.label} className="min-w-0 truncate text-xs font-semibold tracking-[0.5px] uppercase">
                {side.outcome.label}
              </span>
              <OutcomePriceText label={side.outcome.label} price={side.price} movement={movements.get(side.outcome.id)} className="shrink-0 text-base" />
            </div>
            <WeeklyChangeText label={side.outcome.label} change={weeklyChanges?.get(side.outcome.id)} className="text-[11px] font-semibold" />
          </div>
        ))}
      </div>
      <PriceSplitBar sides={sides} className="h-3" />
    </div>
  )
}
