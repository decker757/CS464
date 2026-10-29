import type { OutcomePrice } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { formatPrice } from '../../utils/formatPrice'
import Card from '../ui/Card'
import { CheckIcon } from '../ui/icons'
import { selectableClass } from '../ui/selectableClass'

interface OutcomeChoice {
  outcomeId: string
  onChoose: (outcomeId: string) => void
}

interface OutcomePricesProps {
  marketId: string
  outcomes: PublicOutcome[]
  prices: OutcomePrice[] | null
  isVoided: boolean
  winningOutcomeId: string | null
  /**
   * The outcome the trading panel trades, and how to change it; null once
   * trading has stopped, and then every block is a figure rather than a
   * choice, since a block that looks pickable on a market nobody can trade
   * reads as a buy control that does nothing (#429).
   */
  choice: OutcomeChoice | null
  className?: string
}

// Up to ten outcomes: a block never narrower than its price at full size, so
// a ten-outcome market wraps onto a second row instead of squeezing ten
// columns into the page, and a binary one still fills it with two.
const GRID_CLASS = 'grid grid-cols-[repeat(auto-fit,minmax(9.5rem,1fr))] gap-3'

// What an outcome's block says in place of its price once the market has an
// ending, or null while it is still priced. A voided market has no winner, so
// every outcome reads Void rather than Lost (ADR 0025).
function outcomeResultWord({ isVoided, hasWinner, isWinner }: { isVoided: boolean; hasWinner: boolean; isWinner: boolean }): string | null {
  if (isVoided) return 'Void'
  if (!hasWinner) return null
  return isWinner ? 'Won' : 'Lost'
}

// The control's name says the price as well as the outcome, the way the block
// shows them (#429). No price yet is not a price: it says so rather than
// naming a number nobody quoted.
function choiceName(outcome: PublicOutcome, price: OutcomePrice | undefined): string {
  return `${outcome.label}, ${price ? formatPrice(price.price) : 'price loading'}`
}

interface OutcomeFaceProps {
  outcome: PublicOutcome
  price: OutcomePrice | undefined
  resultWord: string | null
  isChosen: boolean
}

// The label and the price, or the result in the price's place. The same on a
// block that is a choice and on one that is only a figure, but for the
// colours a chosen block's navy fill needs.
function OutcomeFace({ outcome, price, resultWord, isChosen }: OutcomeFaceProps) {
  return (
    <>
      {/* break-words: a label can be one unbroken word of 120 characters,
          wider than a block (#516). The padding keeps it clear of the
          chosen mark in the corner. */}
      <p className={`mb-2 px-3 text-[13px] font-semibold tracking-[0.5px] break-words uppercase ${isChosen ? 'text-white/80' : 'text-muted'}`}>
        {outcome.label}
      </p>
      {/* Once the result is decided it takes the price's place (#429): the
          last trade's price under "Approved outcome: Yes" read as the market
          doubting its own answer. The public read names the winner only once
          a second administrator has approved it (market-service.md), so a
          pending market keeps its price. The price is not drawn then, so no
          price frame can paint over the result. */}
      {resultWord ? (
        <p aria-label={`${outcome.label} result`} className={`mb-1 text-[32px] font-extrabold tracking-[-1px] ${resultWord === 'Won' ? 'text-smu-navy' : 'text-subtle'}`}>
          {resultWord}
        </p>
      ) : (
        <>
          <p aria-label={`${outcome.label} price`} className={`mb-1 text-[32px] font-extrabold tracking-[-1px] ${isChosen ? 'text-white' : 'text-smu-navy'}`}>
            {price ? formatPrice(price.price) : '—'}
          </p>
          <p className={`text-xs ${isChosen ? 'text-white/75' : 'text-subtle'}`}>{price ? 'implied probability' : 'loading price…'}</p>
        </>
      )}
    </>
  )
}

/**
 * Each outcome's price, and — while the market trades — the control that
 * picks which outcome the trading panel trades (#429). Polymarket and Kalshi
 * make the price the button, so choosing a side and seeing what it costs are
 * one control rather than a block nobody could click above an unpriced toggle.
 *
 * Native radios, under a covering input the whole block answers to: one tab
 * stop, the arrow keys and the checked state come with them, and only the
 * look is written here. The chosen block is filled, which is colour, and
 * also carries a tick where the others have an empty ring, which is not.
 */
export default function OutcomePrices({ marketId, outcomes, prices, isVoided, winningOutcomeId, choice, className = '' }: OutcomePricesProps) {
  const hasWinner = winningOutcomeId !== null

  if (!choice) {
    return (
      <div className={`${GRID_CLASS} ${className}`}>
        {outcomes.map(outcome => {
          const isWinner = outcome.id === winningOutcomeId
          return (
            <Card key={outcome.id} borderColor={isWinner ? 'border-info/40' : undefined} className="min-w-0 px-6 py-5 text-center">
              <OutcomeFace
                outcome={outcome}
                price={prices?.find(p => p.outcome_id === outcome.id)}
                resultWord={outcomeResultWord({ isVoided, hasWinner, isWinner })}
                isChosen={false}
              />
            </Card>
          )
        })}
      </div>
    )
  }

  return (
    <div role="radiogroup" aria-label="Outcome to trade" className={`${GRID_CLASS} ${className}`}>
      {outcomes.map(outcome => {
        const price = prices?.find(p => p.outcome_id === outcome.id)
        const isChosen = outcome.id === choice.outcomeId
        const blockClass = isChosen ? selectableClass(true) : `bg-white ${selectableClass(false)} hover:shadow-card-hover`
        return (
          <div
            key={outcome.id}
            className={`relative min-w-0 rounded-2xl border px-6 py-5 text-center shadow-card transition has-focus-visible:ring-3 has-focus-visible:ring-smu-gold/60 ${blockClass}`}
          >
            <input
              type="radio"
              name={`outcome-to-trade-${marketId}`}
              value={outcome.id}
              checked={isChosen}
              onChange={() => choice.onChoose(outcome.id)}
              aria-label={choiceName(outcome, price)}
              className="absolute inset-0 size-full cursor-pointer appearance-none rounded-2xl opacity-0"
            />
            <span
              aria-hidden="true"
              className={`pointer-events-none absolute top-3 right-3 flex size-5 items-center justify-center rounded-full border ${
                isChosen ? 'border-smu-gold bg-smu-gold text-smu-navy' : 'border-smu-navy/30'
              }`}
            >
              {isChosen && <CheckIcon />}
            </span>
            <OutcomeFace outcome={outcome} price={price} resultWord={null} isChosen={isChosen} />
          </div>
        )
      })}
    </div>
  )
}
