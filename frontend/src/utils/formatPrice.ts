import { formatCreditsPrecise } from './formatCredits'

// A price is a probability sent as a decimal string, "0.6234", and is shown as
// a percentage to one decimal place, "62.3%". That is the price's first three
// fraction digits, rounded half up on the fourth, the way the ledger rounds.
//
// Done on the digits rather than through parseFloat: 0.1235 is 0.12349999… as
// a float, so `(parseFloat(price) * 100).toFixed(1)` shows 12.3% for a price
// that is exactly halfway to 12.4%. docs/api/realtime-service.md.
//
// Anything that is not a plain decimal, like "1E-4", shows "—" rather than
// letting BigInt throw during render and take the whole page down.
const PLAIN_DECIMAL = /^\d+(\.\d+)?$/

export function formatPrice(price: string): string {
  const points = formatPercentagePoints(price)
  return points === '—' ? points : `${points}%`
}

/**
 * A probability, or a distance between two of them, as percentage points to
 * one decimal place and nothing else: "0.1200" is "12.0". Rounded exactly as
 * `formatPrice` rounds, because it is `formatPrice`'s own arithmetic, so a
 * card's change and the prices it is the difference of round the same way
 * (#564). Unsigned: a caller with a signed change passes its magnitude and
 * says the direction itself.
 */
export function formatPercentagePoints(value: string): string {
  if (!PLAIN_DECIMAL.test(value)) return '—'

  const [whole, fraction = ''] = value.split('.')
  const keptDigits = fraction.padEnd(3, '0').slice(0, 3)
  const firstDroppedDigit = fraction[3] ?? '0'

  let tenthsOfAPercent = BigInt(whole + keptDigits)
  if (firstDroppedDigit >= '5') tenthsOfAPercent += 1n

  const digits = tenthsOfAPercent.toString().padStart(2, '0')
  return `${digits.slice(0, -1)}.${digits.slice(-1)}`
}

/**
 * A limit price, in the units the order ticket's field takes it: the
 * ledger's own decimal at four places, "0.5500", not `formatPrice`'s
 * percentage. A trader who typed 0.55 and reads back 55.0% — in the list of
 * their orders, or in an `order_crosses_book` refusal telling them where the
 * limit has to go — has a conversion to do before they can act on it (#61's
 * review). The marginal price a market quotes stays a percentage.
 */
export function formatLimitPrice(price: string): string {
  return formatCreditsPrecise(price)
}
