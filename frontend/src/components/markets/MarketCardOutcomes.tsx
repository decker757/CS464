import type { OutcomePrice, PriceState } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { usePriceMovements } from '../../hooks/usePriceMovements'
import { formatPrice } from '../../utils/formatPrice'
import { movementLabel, type PriceMovement } from './priceMovement'

interface MarketCardOutcomesProps {
  /** The market's outcomes, in position order. */
  outcomes: PublicOutcome[]
  /** The market's price snapshot; undefined until it arrives, or if the read failed. */
  snapshot?: PriceState
  /** False for a voided market, which has no price to show (#526). */
  showsPrices: boolean
  /** True for a book nobody has traded, whose prices are only its seed (#445). */
  hasNeverTraded: boolean
}

interface OutcomePriceTextProps {
  label: string
  price: string | undefined
  movement: PriceMovement | undefined
  className: string
}

/** An outcome and the price the snapshot quotes for it. */
interface PricedOutcome {
  outcome: PublicOutcome
  price: string
}

/** How one side of the split is coloured: as a block, and as a bar segment. */
interface SideTone {
  side: string
  segment: string
}

// Green for Yes and red for No, the pairing the trading panel already uses
// for Buy and Sell (#441). Each side's text is its tone's `-strong` shade: the
// base tone on a 10% tint of itself reaches only about 4.4:1, short of AA at
// this size (#380), which is why `Badge` pairs them the same way.
const YES_TONE: SideTone = { side: 'border-success bg-success/10 text-success-strong', segment: 'bg-success' }
const NO_TONE: SideTone = { side: 'border-danger bg-danger/10 text-danger-strong', segment: 'bg-danger' }
// Every other outcome takes the brand's two tones, alternating by position.
// Green and red say more than "a different side": they say yes and no, and on
// "Lakers" against "Celtics" that would make one team the answer and the other
// the refusal. Navy and gold tell neighbours apart without taking a side.
const BRAND_TONES: SideTone[] = [
  { side: 'border-smu-navy bg-smu-navy/10 text-smu-navy', segment: 'bg-smu-navy' },
  { side: 'border-smu-gold bg-smu-gold/10 text-smu-gold-text', segment: 'bg-smu-gold' },
]

// The narrowest a side may be drawn, so that an outcome the crowd has all but
// written off still shows its label and its price. Below its share it stops
// shrinking, and the other side takes what is left.
const SIDE_MIN_WIDTH = 'min-w-[96px]'

// Each side's share of the row follows its price: flex-grow takes the decimal
// as the ledger sends it, so a share is never parsed or rounded on the way.
// The basis is zero, so the shares divide the whole row rather than whatever
// the labels leave over. The grow animates, so a trade slides the split
// rather than snapping it — the change itself is visible (#444).
const PROPORTIONAL = 'basis-0 transition-[flex-grow] duration-500 motion-reduce:transition-none'

/** Every outcome with its price, or null unless the snapshot prices them all:
 *  a split with a side missing would hand that side's share to the others. */
function pricedOutcomesOf(outcomes: PublicOutcome[], prices: OutcomePrice[]): PricedOutcome[] | null {
  const priced: PricedOutcome[] = []
  for (const outcome of outcomes) {
    const quoted = prices.find(price => price.outcome_id === outcome.id)
    if (!quoted) return null
    priced.push({ outcome, price: quoted.price })
  }
  return priced
}

/** Each outcome's tone, in the order of `outcomes`. Green and red only for a
 *  market whose two outcomes are Yes and No, in either order and any case. */
function tonesOf(outcomes: PublicOutcome[]): SideTone[] {
  const labels = outcomes.map(outcome => outcome.label.trim().toLowerCase())
  const isYesNo = labels.length === 2 && labels.includes('yes') && labels.includes('no')
  return labels.map((label, index) => {
    if (isYesNo) return label === 'yes' ? YES_TONE : NO_TONE
    return BRAND_TONES[index % BRAND_TONES.length]
  })
}

// An outcome's price, with its cue when it has just moved. Its colour comes
// from whatever it sits on, arrow included: on a green Yes side a red ▼ would
// argue with the side's own colour about what the red means, and the arrow's
// shape already says which way without it (#441).
function OutcomePriceText({ label, price, movement, className }: OutcomePriceTextProps) {
  return (
    <span aria-label={movement ? movementLabel(label, movement) : `${label} price`} className={`flex items-center gap-1 font-bold ${className}`}>
      {/* The arrow is the cue that is not colour, and the label says it in
          words for a trader who hears the card instead (#444 AC 3). */}
      {movement && <span aria-hidden="true">{movement === 'up' ? '▲' : '▼'}</span>}
      {price ? formatPrice(price) : '—'}
    </span>
  )
}

// A browse card's outcomes and their prices ([X-1] #441).
//
// The sides used to be equal grey chips at 50/50 and at 89/11 alike, so which
// way the crowd leans had to be read off two small numbers. Now a priced
// binary market divides the card's width between its two sides, in proportion
// to their prices: a coin flip looks like one, and a lopsided market looks
// lopsided from across the room. The card is the chart.
//
// More outcomes than two cannot be divided that way — five sides at their
// minimum width do not fit a card, and five slivers of truncated text say
// nothing. They keep one readable chip each and draw the split as one thin
// bar above them, a segment per outcome in the same order.
//
// Only a price draws a split. A card still waiting for one, refused one, or
// with none to show — untraded, or voided — keeps its plain equal chips,
// because two equal sides would read as a 50/50 that nobody holds (#431).
export default function MarketCardOutcomes({ outcomes, snapshot, showsPrices, hasNeverTraded }: MarketCardOutcomesProps) {
  // Which outcomes have just moved, and which way, for the cue on each price.
  const movements = usePriceMovements(snapshot)
  const pricedOutcomes = showsPrices && !hasNeverTraded && snapshot ? pricedOutcomesOf(outcomes, snapshot.prices) : null
  const tones = tonesOf(outcomes)

  if (pricedOutcomes && pricedOutcomes.length === 2) {
    return (
      <div className="mt-4 flex gap-1">
        {pricedOutcomes.map(({ outcome, price }, index) => (
          <div
            key={outcome.id}
            style={{ flexGrow: price }}
            // The second side reads from the right edge, so the two labels sit
            // at the ends of the split rather than both crowding its left.
            className={`flex flex-col gap-0.5 rounded-control border-b-4 px-3 py-2 ${SIDE_MIN_WIDTH} ${PROPORTIONAL} ${tones[index].side} ${index === 1 ? 'items-end' : 'items-start'}`}
          >
            {/* max-w-full: a label can be one unbroken word, and a side has a
                fixed minimum rather than room for it (#516). */}
            <span title={outcome.label} className="max-w-full truncate text-xs font-semibold tracking-[0.5px] uppercase">
              {outcome.label}
            </span>
            <OutcomePriceText label={outcome.label} price={price} movement={movements.get(outcome.id)} className="text-base" />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="mt-4">
      {pricedOutcomes && (
        // A picture of the chips below it, which say every figure in it in
        // text; the title names a segment too thin to tell apart by eye.
        <div aria-hidden="true" className="mb-2 flex h-1.5 gap-0.5 overflow-hidden rounded-full">
          {pricedOutcomes.map(({ outcome, price }, index) => (
            <div
              key={outcome.id}
              title={`${outcome.label} ${formatPrice(price)}`}
              style={{ flexGrow: price }}
              className={`${PROPORTIONAL} ${tones[index].segment}`}
            />
          ))}
        </div>
      )}
      {/* Wraps onto more rows for a market with many outcomes (up to ten). */}
      <div className="flex flex-wrap gap-2">
        {outcomes.map(outcome => (
          <div key={outcome.id} className="flex min-w-[120px] flex-1 items-center justify-between gap-2 rounded-control bg-smu-cream px-3 py-2">
            <span title={outcome.label} className="truncate text-xs font-semibold tracking-[0.5px] text-muted uppercase">
              {outcome.label}
            </span>
            {/* An untraded market keeps its outcome names (#214) and shows
                no number at all. A dash would say the wrong thing: that is
                what a price still on its way, or one that could not be
                read, reads as — and the line below the chips says which
                of those two it is (#431). */}
            {showsPrices && !hasNeverTraded && (
              <OutcomePriceText
                label={outcome.label}
                price={snapshot?.prices.find(price => price.outcome_id === outcome.id)?.price}
                movement={movements.get(outcome.id)}
                className="shrink-0 text-sm text-smu-navy"
              />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
