import type { PriceState } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import type { PriceMovement } from './priceMovement'
import OutcomePriceText from './OutcomePriceText'
import { SIDE_TONE_EDGE, type PricedSide } from './outcomeSides'
import PriceSplitBar from './PriceSplitBar'
import WeeklyChangeText from './WeeklyChangeText'

interface OutcomeChipsProps {
  outcomes: PublicOutcome[]
  snapshot?: PriceState
  /** False for a voided or untraded market, which shows no number at all. */
  hasPrices: boolean
  /** Every outcome priced, when the snapshot prices them all; else null. */
  sides: PricedSide[] | null
  movements: Map<string, PriceMovement>
  /** Each outcome's change over the week, by id; null when there is none (#564). */
  weeklyChanges: Map<string, string> | null
}

// The chip's background while a price is marked as moved. A tint of the tone
// rather than the tone itself: this sits behind an 11px label and a bold
// price, both of which have to stay readable on it (#380).
function movementTint(movement: PriceMovement | undefined): string {
  if (movement === 'up') return 'bg-success/10'
  if (movement === 'down') return 'bg-danger/10'
  return 'bg-smu-cream'
}

// A browse card's outcomes as one chip each: every market with more than two
// outcomes, and any market with no price to split — still on its way, refused,
// untraded or voided — where a split would read as a 50/50 nobody holds
// (#431, #441).
//
// A priced market gets one thin bar over its chips, split at the prices. Its
// segments alternate two tones, so each chip carries its segment's colour
// along its bottom edge: the chips wrap, and the colour is how a reader finds
// the chip a segment belongs to.
export default function OutcomeChips({ outcomes, snapshot, hasPrices, sides, movements, weeklyChanges }: OutcomeChipsProps) {
  return (
    <div className="mt-4">
      {sides && <PriceSplitBar sides={sides} className="mb-2 h-1.5" />}
      {/* Wraps onto more rows for a market with many outcomes (up to ten). */}
      <div className="flex flex-wrap gap-2">
        {outcomes.map((outcome, index) => {
          const movement = movements.get(outcome.id)
          const edge = sides ? `border-b-4 ${SIDE_TONE_EDGE[sides[index].tone]}` : ''
          return (
            <div
              key={outcome.id}
              className={`flex min-w-[120px] flex-1 items-center justify-between gap-2 rounded-control px-3 py-2 transition-colors duration-500 motion-reduce:transition-none ${movementTint(movement)} ${edge}`}
            >
              <span title={outcome.label} className="truncate text-xs font-semibold tracking-[0.5px] text-muted uppercase">
                {outcome.label}
              </span>
              {/* An untraded market keeps its outcome names (#214) and shows
                  no number at all. A dash would say the wrong thing: that is
                  what a price still on its way, or one that could not be
                  read, reads as — and the line below the chips says which
                  of those two it is (#431). */}
              {hasPrices && (
                <div className="flex shrink-0 flex-col items-end">
                  <OutcomePriceText
                    label={outcome.label}
                    price={snapshot?.prices.find(price => price.outcome_id === outcome.id)?.price}
                    movement={movement}
                    className="text-sm text-smu-navy"
                  />
                  <WeeklyChangeText label={outcome.label} change={weeklyChanges?.get(outcome.id)} className="text-[11px] text-secondary" />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
