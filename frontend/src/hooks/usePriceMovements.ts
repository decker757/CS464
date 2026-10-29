import { useEffect, useState } from 'react'
import type { PriceState } from '../api/ledgerApi'
import { priceMovementOf, type PriceMovement } from '../components/markets/priceMovement'

// How long a price that has just moved stays marked. Long enough to catch the
// eye on a grid of fifty cards, short enough that two trades in quick
// succession read as two events ([X-1] #444 AC 3).
const FLASH_MS = 1_500

// One identity for every card with nothing moving, so the common case
// allocates nothing.
const EMPTY_MOVEMENTS = new Map<string, PriceMovement>()

// Which outcomes moved between two snapshots of the same market, and which
// way. Empty when either is missing: no previous price is first paint, not a
// move, and every card would otherwise flash on load — which says "everything
// just changed" on a page where nothing has.
function movementsBetween(
  before: PriceState | undefined,
  after: PriceState | undefined,
): Map<string, PriceMovement> {
  const moved = new Map<string, PriceMovement>()
  if (!before || !after) return moved

  for (const price of after.prices) {
    const previous = before.prices.find(candidate => candidate.outcome_id === price.outcome_id)
    if (!previous) continue
    const movement = priceMovementOf(previous.price, price.price)
    if (movement) moved.set(price.outcome_id, movement)
  }
  return moved
}

// Which of a market's outcomes have just moved, and which way, by outcome id.
// A price arrives on the socket within a moment of the trade that moved it
// ([X-1] #444), and a number that repaints silently on a grid of fifty cards
// is a number nobody sees change — so the card marks it, briefly.
//
// Derived from the snapshot before this one rather than set in an effect
// afterwards: an effect that called setState synchronously would render the
// card a second time for every price frame, and a trade repaints a grid of
// fifty of them.
export function usePriceMovements(snapshot: PriceState | undefined): Map<string, PriceMovement> {
  // The pair this card has rendered: the snapshot on screen, and the one
  // before it. Kept in state because rendering reads both — React's own
  // "adjusting state when a prop changes" — and kept as a pair because
  // replacing a single value during render would leave the next pass
  // comparing a snapshot with itself.
  const [seen, setSeen] = useState<{ previous?: PriceState; current?: PriceState }>({})
  if (snapshot !== seen.current) setSeen({ previous: seen.current, current: snapshot })

  const moved = movementsBetween(seen.previous, seen.current)
  // The cue is temporary, so a timer takes it down. Keyed on the version that
  // moved, so an ordinary re-render — the clock ticking, a holding arriving —
  // neither restarts it nor puts the cue back up.
  const movedVersion = moved.size > 0 ? (seen.current?.state_version ?? null) : null
  const [clearedVersion, setClearedVersion] = useState<number | null>(null)
  useEffect(() => {
    if (movedVersion === null) return
    const clear = setTimeout(() => setClearedVersion(movedVersion), FLASH_MS)
    return () => clearTimeout(clear)
  }, [movedVersion])

  return movedVersion !== null && movedVersion !== clearedVersion ? moved : EMPTY_MOVEMENTS
}
