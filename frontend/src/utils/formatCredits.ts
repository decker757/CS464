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
  return /^0*$/.test(wholeRaw) && /^0*$/.test(fractionRaw)
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
  if (value.startsWith('-')) return 'text-danger'
  return 'text-success'
}

// Credits are whole numbers in practice, so display reads the integer part
// directly off the string instead of parsing it as a float and rounding.
// BigInt is exact over that integer part — no float involved — and
// toLocaleString adds the thousands separators and the minus sign.
export function formatCredits(balance: string): string {
  if (!isPlainCredits(balance)) return '—'

  const integerPart = balance.split('.')[0] || '0'
  return BigInt(integerPart).toLocaleString('en-US')
}

// For a value where the decimal places matter — a trade's exact cost, a
// position's cost basis or P&L — rather than a balance read as a whole
// number. Keeps exactly 4 decimal places, padding or truncating the string
// as written rather than rounding a parsed float. BigInt is exact over the
// whole-number part, same as formatCredits; the four decimal digits are
// joined on afterwards since BigInt has no fractional part to keep.
export function formatCreditsPrecise(value: string, { showSign = false } = {}): string {
  if (!isPlainCredits(value)) return '—'

  const { digits, isNegative } = splitSign(value)
  const [wholeRaw, fractionRaw = ''] = digits.split('.')
  const integerPart = BigInt(wholeRaw || '0').toLocaleString('en-US')
  const fractionPart = (fractionRaw + '0000').slice(0, 4)
  let sign = ''
  if (isNegative) sign = '-'
  else if (showSign && !isZeroCredits(value)) sign = '+'
  return `${sign}${integerPart}.${fractionPart}`
}

// `average_price` / `average_entry_price`: credits per share, not a
// probability — it can reach 1.0000 and pass it on a skewed book
// (ledger-service.md), so it is never shown as a percentage. #247 picks this
// format as the one shown on every screen (frontend/CLAUDE.md); every caller
// goes through this function so the three screens cannot drift again.
export function formatAveragePrice(value: string): string {
  return formatCreditsPrecise(value)
}
