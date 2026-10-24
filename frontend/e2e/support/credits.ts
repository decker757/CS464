// #373. The expected credits figures of the end-to-end specs, worked out from
// the grant the ledger reports. Deliberately not the app's own formatters in
// src/utils/formatCredits.ts: an expectation computed by the code under test
// cannot catch that code being wrong.
//
// An amount is a decimal string of at most four places, the way the ledger
// sends one, and is held here as a whole number of ten-thousandths, so the
// arithmetic is exact. Every function here takes only amounts of zero or
// more, and throws on anything else, a negative P&L included.

const UNITS_PER_CREDIT = 10_000n
const PLACES = 4
const PLAIN_AMOUNT = /^(\d+)(?:\.(\d{1,4}))?$/

function unitsOf(amount: string): bigint {
  const match = PLAIN_AMOUNT.exec(amount)
  if (!match) throw new Error(`not a credits amount: ${amount}`)
  const whole = match[1]
  const fraction = (match[2] ?? '').padEnd(PLACES, '0')
  return BigInt(whole) * UNITS_PER_CREDIT + BigInt(fraction)
}

function wholePartOf(units: bigint): bigint {
  return units / UNITS_PER_CREDIT
}

function fractionOf(units: bigint): string {
  return (units % UNITS_PER_CREDIT).toString().padStart(PLACES, '0')
}

function amountOf(units: bigint): string {
  return `${wholePartOf(units)}.${fractionOf(units)}`
}

function groupThousands(whole: bigint): string {
  return whole.toLocaleString('en-US')
}

/** `amount` less `spent`, exactly, as a plain four-place amount. */
export function creditsLess(amount: string, spent: string): string {
  const left = unitsOf(amount) - unitsOf(spent)
  if (left < 0n) throw new Error(`${amount} less ${spent} is below zero`)
  return amountOf(left)
}

/** `amount` plus `more`, exactly, as a plain four-place amount. */
export function creditsPlus(amount: string, more: string): string {
  return amountOf(unitsOf(amount) + unitsOf(more))
}

export function isBelow(amount: string, floor: string): boolean {
  return unitsOf(amount) < unitsOf(floor)
}

/**
 * As the navbar's readout and the portfolio's cards show an amount: rounded
 * to the cent, half away from zero, and with no decimal part at all when it
 * comes to a whole number of credits. `1,234.57`, and `1,234` for `1234.0000`.
 *
 * Half away from zero is the ledger's own ROUND_HALF_UP, which is the rule
 * the app's own formatter follows (#443). Worked out here in ten-thousandths
 * rather than borrowed from `src/utils/formatCredits.ts`, for this module's
 * own reason: an expectation computed by the code under test cannot catch
 * that code being wrong.
 */
export function formatRoundedCredits(amount: string): string {
  const units = unitsOf(amount)
  // Ten-thousandths to hundredths: the kept hundredths, plus one if the
  // ten-thousandths left over reach half of one of them.
  const perCent = UNITS_PER_CREDIT / 100n
  const cents = units / perCent + (units % perCent >= perCent / 2n ? 1n : 0n)
  const whole = groupThousands(cents / 100n)
  if (cents % 100n === 0n) return whole
  return `${whole}.${(cents % 100n).toString().padStart(2, '0')}`
}

/** As the portfolio's position rows, the history and the leaderboard show an amount: four places. `1,234.5678`. */
export function formatPreciseCredits(amount: string): string {
  const units = unitsOf(amount)
  return `${groupThousands(wholePartOf(units))}.${fractionOf(units)}`
}

/** As the history shows money paid in, the grant among it: `+1,234.5678`, and zero unsigned. */
export function formatSignedCredits(amount: string): string {
  const precise = formatPreciseCredits(amount)
  if (unitsOf(amount) === 0n) return precise
  return `+${precise}`
}
