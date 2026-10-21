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

/** As the navbar shows a balance: whole credits, the fraction dropped rather than rounded. `1,234`. */
export function formatWholeCredits(amount: string): string {
  return groupThousands(wholePartOf(unitsOf(amount)))
}

/** As the portfolio, history and leaderboard show an amount: four places. `1,234.5678`. */
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
