import { compareCredits } from '../../utils/formatCredits'

/**
 * Which way a price last moved, for the cue a card shows when it changes
 * ([X-1] #444 AC 3).
 *
 * Prices are decimal strings from the ledger's exact arithmetic and are never
 * parsed into a float (ledgerApi.ts), so they are ordered the way credits are:
 * `compareCredits` compares the digits, ignores trailing zeros, and orders a
 * value that is not a plain decimal as unchanged rather than throwing.
 */
export type PriceMovement = 'up' | 'down'

/** Which way the price went, or null if it did not move. */
export function priceMovementOf(previous: string, current: string): PriceMovement | null {
  const order = compareCredits(current, previous)
  if (order === 0) return null
  return order > 0 ? 'up' : 'down'
}

/** What a screen reader says about a price that has just moved. */
export function movementLabel(outcomeLabel: string, movement: PriceMovement): string {
  return `${outcomeLabel} price ${movement === 'up' ? 'rose' : 'fell'}`
}
