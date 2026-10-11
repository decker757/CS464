// Balances and amounts arrive as decimal strings, never JSON numbers
// (ledger-service.md): a JSON number is an IEEE double by the time the
// browser parses it, and a credit total off by a floating-point epsilon is a
// bug report about money. Every formatter here works on the string's own
// digits; none of them parseFloat it.
//
// Anything that is not a plain signed decimal shows "—" rather than letting
// BigInt throw during render and take the whole page down (formatPrice.ts).
const PLAIN_SIGNED_DECIMAL = /^-?\d+(\.\d+)?$/

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
  if (!PLAIN_SIGNED_DECIMAL.test(balance)) return '—'

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
  if (!PLAIN_SIGNED_DECIMAL.test(value)) return '—'

  const { digits, isNegative } = splitSign(value)
  const [wholeRaw, fractionRaw = ''] = digits.split('.')
  const integerPart = BigInt(wholeRaw || '0').toLocaleString('en-US')
  const fractionPart = (fractionRaw + '0000').slice(0, 4)
  let sign = ''
  if (isNegative) sign = '-'
  else if (showSign && !isZeroCredits(value)) sign = '+'
  return `${sign}${integerPart}.${fractionPart}`
}
