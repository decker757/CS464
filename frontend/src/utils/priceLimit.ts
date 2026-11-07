import type { TradeSide } from '../api/ledgerApi'
import { creditsFromUnits, isPlainCredits, unsignedCredits } from './formatCredits'

// How far a trade's price may move between its quote and the ledger pricing
// it, as a percentage of the quoted total (#644: 1%, decided by Ernest on
// 2026-11-05). Enough to absorb a couple of one-share trades landing first on
// a fresh market at the default liquidity.
const TOLERANCE_PERCENT = 1n

// The ledger's tick: limits are sent at four decimal places.
const TICK_PLACES = 4

/**
 * The price limit a trade sends with its quote (#644): the most a buy will
 * pay (`max_cost`) or the least a sell will accept (`min_proceeds`), as a
 * decimal string at four places.
 *
 * A buy may pay 1% more than quoted, rounded up to the tick, and a sell may
 * receive 1% less, rounded down: rounding the other way would make the limit
 * stricter than the trader was told, by up to a tick. The quoted total is
 * read as a magnitude, because a buy's arrives negative (credits out).
 *
 * This is the one money figure the browser sends rather than displays, so it
 * is worked out here, on the string's digits with BigInt ([B-1] #359).
 *
 * Retrying an unconfirmed trade re-quotes it, so the retry carries the same
 * idempotency key with a new limit. That replays the first trade only while
 * the ledger leaves the limit out of its reused-key check, as it does
 * state_version (ledger-service.md, and `_matches` in the ledger's
 * service/trading.py); counting it would refuse the retry as
 * idempotency_key_reused, and the next click would buy twice.
 *
 * Null for a total that is not a plain decimal: the trade then sends no
 * limit, rather than a wrong one.
 */
export function priceLimitFor(side: TradeSide, quotedTotal: string): string | null {
  if (!isPlainCredits(quotedTotal)) return null

  const [whole, fraction = ''] = unsignedCredits(quotedTotal).split('.')
  // The total as a whole number of its own smallest unit, and that unit.
  const quotedUnits = BigInt(whole + fraction)
  const quotedScale = 10n ** BigInt(fraction.length)

  const percent = side === 'buy' ? 100n + TOLERANCE_PERCENT : 100n - TOLERANCE_PERCENT
  const numerator = quotedUnits * percent * 10n ** BigInt(TICK_PLACES)
  const denominator = 100n * quotedScale
  const tickUnits = side === 'buy'
    ? (numerator + denominator - 1n) / denominator
    : numerator / denominator
  return creditsFromUnits(tickUnits, TICK_PLACES)
}
