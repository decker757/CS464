// Balances and amounts arrive as decimal strings, never JSON numbers
// (ledger-service.md): a JSON number is an IEEE double by the time the
// browser parses it, and a credit total off by a floating-point epsilon is a
// bug report about money. Every formatter here works on the string's own
// digits; none of them parseFloat it.
//
// Anything that is not a plain signed decimal shows "—" rather than letting
// BigInt throw during render and take the whole page down (formatPrice.ts).
// The sign, the whole part and the fraction are captured, so the comparison
// and the subtraction below can read the digits they work on without a
// second regex saying the same thing. Everything else here only asks whether
// a value matches at all.
const PLAIN_SIGNED_DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/

const ONLY_ZEROS = /^0*$/

// Credits are computed at four decimal places because that is the scale the
// pricing engine works in (ledger-service.md), and shown at two, because that
// is how an amount of money is read. Nobody holds ten-thousandths of a credit,
// and a screen of figures ending ".0000" is mostly zeroes (#443). The four
// places survive where they are the point rather than the noise: a table of
// movements or positions, beside an average price that cannot be rounded to
// the cent (#247).
const DISPLAY_SCALE = 2
const EXACT_SCALE = 4

/** Whether a value is a plain signed decimal, and so safe to format or
 * compare. A caller that would rather drop such a value than show "—" for
 * it — picking the worst of several figures, say — asks with this first. */
export function isPlainCredits(value: string): boolean {
  return PLAIN_SIGNED_DECIMAL.test(value)
}

function splitSign(value: string): { digits: string; isNegative: boolean } {
  return value.startsWith('-') ? { digits: value.slice(1), isNegative: true } : { digits: value, isNegative: false }
}

/** The magnitude of a decimal string, sign dropped — for a cost or proceeds
 * display where the direction is already said in words ("Cost", "Bought"). */
export function unsignedCredits(value: string): string {
  return splitSign(value).digits
}

/** Whether a decimal string is exactly zero — "-0.0000" included, since
 * splitSign only strips a leading "-" and never changes what it means. */
export function isZeroCredits(value: string): boolean {
  const { digits } = splitSign(value)
  const [wholeRaw, fractionRaw = ''] = digits.split('.')
  return ONLY_ZEROS.test(wholeRaw) && ONLY_ZEROS.test(fractionRaw)
}

/** Whether a decimal string is below zero — "-0.0000" is not. A residue the
 * pool cannot cover, on the exposure summary and in its table alike (#377). */
export function isNegativeCredits(value: string): boolean {
  return value.startsWith('-') && !isZeroCredits(value)
}

// Two decimal strings as two plain integers in the same unit: each fraction
// padded to the longer of the two, so BigInt reads the digits exactly where
// two parsed floats would not. Null when either side is not a plain decimal,
// which is how both callers below bail out instead of letting BigInt throw
// during render.
function toSameScale(left: string, right: string): { leftUnits: bigint; rightUnits: bigint; fractionDigits: number } | null {
  const leftParts = PLAIN_SIGNED_DECIMAL.exec(left)
  const rightParts = PLAIN_SIGNED_DECIMAL.exec(right)
  if (!leftParts || !rightParts) return null

  const [, leftSign, leftWhole, leftFraction = ''] = leftParts
  const [, rightSign, rightWhole, rightFraction = ''] = rightParts
  const fractionDigits = Math.max(leftFraction.length, rightFraction.length)
  return {
    leftUnits: BigInt(leftSign + leftWhole + leftFraction.padEnd(fractionDigits, '0')),
    rightUnits: BigInt(rightSign + rightWhole + rightFraction.padEnd(fractionDigits, '0')),
    fractionDigits,
  }
}

/**
 * -1, 0 or 1: whether `left` is less than, the same as, or more than `right`.
 *
 * A comparison, not arithmetic — nothing here negates a figure, and the
 * caller still displays the strings it was given. A value that is not a
 * plain decimal orders as equal rather than letting BigInt throw during
 * render; `isPlainCredits` is how a caller drops one instead.
 */
export function compareCredits(left: string, right: string): number {
  const scaled = toSameScale(left, right)
  if (!scaled) return 0

  if (scaled.leftUnits < scaled.rightUnits) return -1
  return scaled.leftUnits > scaled.rightUnits ? 1 : 0
}

/**
 * `left` minus `right`, as a decimal string — the one piece of arithmetic in
 * this file, and exact.
 *
 * A buy's profit is what it pays if it wins minus what the server charged
 * for it (#357), and both arrive as decimal strings. BigInt subtracts the
 * scaled integers without a float anywhere, and the digits go back together
 * with the decimal places the longer of the two inputs had, so the answer is
 * as precise as the figures it came from and no more.
 *
 * Null, rather than a dash, when either side is not a plain decimal: this
 * returns a credits value for a caller to format, and "—" is not one. The
 * caller drops the figure, the way `isPlainCredits`'s note describes.
 */
export function subtractCredits(left: string, right: string): string | null {
  const scaled = toSameScale(left, right)
  if (!scaled) return null

  const difference = scaled.leftUnits - scaled.rightUnits
  const sign = difference < 0n ? '-' : ''
  const digits = (difference < 0n ? -difference : difference).toString()
  if (scaled.fractionDigits === 0) return sign + digits

  // One digit more than the fraction needs, so a value below 1 keeps its
  // leading zero: 4 units at 4 decimal places is "0.0004", not ".0004".
  const padded = digits.padStart(scaled.fractionDigits + 1, '0')
  const whole = padded.slice(0, padded.length - scaled.fractionDigits)
  return `${sign}${whole}.${padded.slice(padded.length - scaled.fractionDigits)}`
}

// A fresh position's unrealized P&L is zero, not a gain — ADR 0018/[T-4] #24
// say it is never a gain on a book nobody else has traded, and the same is
// true of a signed amount that happens to land on exactly zero. Only colour
// an actual loss or an actual gain; zero stays neutral.
export function signedAmountColor(value: string): string {
  if (isZeroCredits(value)) return 'text-muted'
  if (isNegativeCredits(value)) return 'text-danger'
  return 'text-success'
}

interface RoundedCredits {
  isNegative: boolean
  /** The whole credits, digits only — no thousands separators yet. */
  whole: string
  /** Exactly `scale` digits, padded or rounded to get there. */
  fraction: string
  isZero: boolean
}

// A decimal string rounded to `scale` places, half away from zero, on its own
// digits. The digits that are kept are read as one BigInt and the first digit
// dropped decides whether to add one to it, so the carry in 9.999 → 10.00 is
// integer arithmetic and never a float — a credit total off by a float
// epsilon is a bug report about money.
//
// Half away from zero is the ledger's own ROUND_HALF_UP (ledger-service.md),
// so the browser rounds a figure the same way the service that computed it
// does. Truncating instead is what #443 was: a balance of 9,993.8938 read as
// 9,993 in the navbar, 0.89 credits short of the same figure on the page.
function roundCredits(value: string, scale: number): RoundedCredits | null {
  const parts = PLAIN_SIGNED_DECIMAL.exec(value)
  if (!parts) return null

  const [, sign, wholeDigits, fractionDigits = ''] = parts
  const kept = fractionDigits.slice(0, scale).padEnd(scale, '0')
  // The sign is held aside, so rounding this magnitude up rounds away from zero.
  const roundsUp = (fractionDigits[scale] ?? '0') >= '5'
  const units = BigInt(wholeDigits + kept) + (roundsUp ? 1n : 0n)

  // One digit more than the fraction keeps, so an amount below one credit
  // still has a whole part: 7 units at scale 2 is "0.07", not ".07".
  const digits = units.toString().padStart(scale + 1, '0')
  return {
    isNegative: sign === '-',
    whole: digits.slice(0, digits.length - scale),
    fraction: digits.slice(digits.length - scale),
    isZero: units === 0n,
  }
}

// A sign goes on the rounded figure, not on the value behind it: a P&L of
// -0.0001 is shown as "0" and must read flat rather than as "-0", and zero is
// neither a gain nor a loss either way. `showSign` is for a figure whose
// direction is the point, like P&L.
function signOf({ isNegative, isZero }: RoundedCredits, showSign: boolean): string {
  if (isZero) return ''
  if (isNegative) return '-'
  return showSign ? '+' : ''
}

/**
 * A credits figure as a reader of money reads it: thousands separated, two
 * decimal places, and no decimal part at all when the amount comes to a whole
 * number of credits.
 *
 * The one helper for every figure shown on its own — the navbar readout and
 * the portfolio's cards — so those two cannot disagree about the same balance
 * the way they did in #443. A table of movements or positions shows the
 * ledger's own four places through `formatCreditsPrecise` instead.
 */
export function formatCredits(value: string, { showSign = false } = {}): string {
  const rounded = roundCredits(value, DISPLAY_SCALE)
  if (!rounded) return '—'

  const wholePart = BigInt(rounded.whole).toLocaleString('en-US')
  const fractionPart = ONLY_ZEROS.test(rounded.fraction) ? '' : `.${rounded.fraction}`
  return signOf(rounded, showSign) + wholePart + fractionPart
}

/**
 * `value` rounded to the scale `formatCredits` shows, as a decimal string —
 * for a caller that colours a figure as well as printing it, so the colour
 * reads off the number on screen and not a digit nobody can see: an
 * unrealized P&L of -0.0001 shows as "0" and must come out neutral.
 *
 * A credits value and not a formatted figure: no separators and no plus sign,
 * the same contract `subtractCredits` has. Anything the formatters would show
 * "—" for comes back unchanged, so a caller colouring it is no worse off than
 * before.
 */
export function roundedCredits(value: string): string {
  const rounded = roundCredits(value, DISPLAY_SCALE)
  if (!rounded) return value

  return `${rounded.isNegative ? '-' : ''}${rounded.whole}.${rounded.fraction}`
}

/**
 * The same figure at the ledger's own four decimal places, always written in
 * full: a trade's exact amount, a position's cost basis, an average price.
 *
 * A column of these is where the working is checked, which is why it keeps
 * every place the ledger sent and why it never drops a trailing zero: a
 * ragged column is harder to read down than a zero is to ignore.
 */
export function formatCreditsPrecise(value: string, { showSign = false } = {}): string {
  const rounded = roundCredits(value, EXACT_SCALE)
  if (!rounded) return '—'

  return `${signOf(rounded, showSign)}${BigInt(rounded.whole).toLocaleString('en-US')}.${rounded.fraction}`
}

// `average_price` / `average_entry_price`: credits per share, not a
// probability — it can reach 1.0000 and pass it on a skewed book
// (ledger-service.md), so it is never shown as a percentage. #247 picks this
// format as the one shown on every screen (frontend/CLAUDE.md); every caller
// goes through this function so the three screens cannot drift again.
export function formatAveragePrice(value: string): string {
  return formatCreditsPrecise(value)
}
