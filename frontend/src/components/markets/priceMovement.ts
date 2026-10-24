/**
 * Which way a price last moved, for the cue a card shows when it changes
 * ([X-1] #444 AC 3).
 *
 * Prices are decimal strings from the ledger's exact arithmetic and are never
 * parsed into a float to be displayed (ledgerApi.ts). Comparing two of them is
 * the one thing that does need their order, and `Number` is safe here because
 * nothing is stored, rendered or summed from the result: the answer is a
 * direction, and a rounding error at the seventeenth decimal cannot change
 * which of two four-decimal prices is larger.
 */
export type PriceMovement = 'up' | 'down'

/** Which way the price went, or null if it did not move. */
export function priceMovementOf(previous: string, current: string): PriceMovement | null {
  const before = decimalOf(previous)
  const after = decimalOf(current)
  if (before === null || after === null || before === after) return null
  return after > before ? 'up' : 'down'
}

// `Number` is the wrong gate on its own: `Number('')` and `Number(' ')` are
// both 0, so an empty value would read as a price of zero and every card
// would flash on its first real price.
function decimalOf(value: string): number | null {
  if (value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/** What a screen reader says about a price that has just moved. */
export function movementLabel(outcomeLabel: string, movement: PriceMovement): string {
  return `${outcomeLabel} price ${movement === 'up' ? 'rose' : 'fell'}`
}
