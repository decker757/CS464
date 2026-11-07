import type { TradeSide } from '../api/ledgerApi'
import { creditsFromUnits, unitsOfCredits } from './formatCredits'

// How far a trade's price may move between its quote and the ledger pricing
// it, as a percentage of the quoted total (#644, ADR 0029). Enough to absorb
// a couple of one-share trades landing first on a fresh market at the
// default liquidity.
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
 * A retry under the same key with a new limit still replays: ADR 0029, "A
 * replay ignores the limit".
 *
 * Null for a total that is not a plain decimal: the trade then sends no
 * limit, rather than a wrong one.
 */
export function priceLimitFor(side: TradeSide, quotedTotal: string): string | null {
  const quoted = unitsOfCredits(quotedTotal)
  if (!quoted) return null

  const quotedUnits = quoted.units < 0n ? -quoted.units : quoted.units
  const quotedScale = 10n ** BigInt(quoted.places)

  const percent = side === 'buy' ? 100n + TOLERANCE_PERCENT : 100n - TOLERANCE_PERCENT
  const numerator = quotedUnits * percent * 10n ** BigInt(TICK_PLACES)
  const denominator = 100n * quotedScale
  const tickUnits = side === 'buy'
    ? (numerator + denominator - 1n) / denominator
    : numerator / denominator
  return creditsFromUnits(tickUnits, TICK_PLACES)
}
