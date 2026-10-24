import { compareCredits, formatCreditsPrecise } from './formatCredits'

/**
 * What is wrong with a share quantity, if anything — checked here so a
 * malformed value never reaches the ledger, which would answer
 * `invalid_request` instead (ledger-service.md's quantity rule, D-038: > 0,
 * at most 4 decimal places, at most 18 digits in all).
 *
 * Undefined for an empty field: nothing is wrong with a field nobody has
 * filled in yet, and the submit button is what refuses it.
 *
 * On the string's own digits, never parseFloat — the same reason
 * formatCredits.ts gives.
 */
export function quantityError(raw: string): string | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  const match = /^(\d+)(?:\.(\d+))?$/.exec(trimmed)
  if (!match) return 'Enter a number.'
  const [, wholePart, fractionPart = ''] = match
  if (!/[1-9]/.test(trimmed)) return 'Must be greater than zero.'
  if (fractionPart.length > 4) return 'At most 4 decimal places.'
  if (wholePart.length + fractionPart.length > 18) return 'That quantity is too large.'
  return undefined
}

/**
 * What is wrong with a limit price, if anything ([T-6] #26). The ledger takes
 * a decimal string strictly between 0 and 1, with at most four decimal
 * places — so `0.0001` to `0.9999` — and answers `invalid_request` for
 * anything else (ledger-service.md, POST .../orders).
 *
 * Both bounds are exclusive: a limit of 0 or 1 is not a price this market can
 * reach, and the ledger refuses both.
 */
export function limitPriceError(raw: string): string | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  const match = /^(\d+)(?:\.(\d+))?$/.exec(trimmed)
  if (!match) return 'Enter a number.'
  const [, wholePart, fractionPart = ''] = match
  if (fractionPart.length > 4) return 'At most 4 decimal places.'
  // Compared on the digits: "0.0000" and "0" are both zero, and anything
  // with a whole part of 1 or more is at or above the ceiling.
  if (!/[1-9]/.test(trimmed)) return 'Must be above 0.'
  if (BigInt(wholePart) >= 1n) return 'Must be below 1.'
  return undefined
}

/**
 * The line above a sell's quantity field: the whole position, and — when the
 * trader's own open sell orders hold some of it back — how much of it is left
 * to sell. The ledger refuses a sell beyond that rest
 * (`insufficient_shares_held`, ledger-service.md), so the line names it
 * rather than a figure the trader cannot use. Used by the market and limit
 * halves of the order ticket alike.
 */
export function describeSharesToSell(held: string, sellable: string): string {
  if (compareCredits(sellable, held) === 0) return `You hold ${formatCreditsPrecise(held)} shares`
  return `You can sell ${formatCreditsPrecise(sellable)} of your ${formatCreditsPrecise(held)} shares`
}
