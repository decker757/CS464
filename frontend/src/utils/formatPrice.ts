// A price is a probability sent as a decimal string, "0.6234", and is shown as
// a percentage to one decimal place, "62.3%". That is the price's first three
// fraction digits, rounded half up on the fourth, the way the ledger rounds.
//
// Done on the digits rather than through parseFloat: 0.1235 is 0.12349999… as
// a float, so `(parseFloat(price) * 100).toFixed(1)` shows 12.3% for a price
// that is exactly halfway to 12.4%. docs/api/realtime-service.md.
export function formatPrice(price: string): string {
  const [whole, fraction = ''] = price.split('.')
  const keptDigits = fraction.padEnd(3, '0').slice(0, 3)
  const firstDroppedDigit = fraction[3] ?? '0'

  let tenthsOfAPercent = BigInt(whole + keptDigits)
  if (firstDroppedDigit >= '5') tenthsOfAPercent += 1n

  const digits = tenthsOfAPercent.toString().padStart(2, '0')
  return `${digits.slice(0, -1)}.${digits.slice(-1)}%`
}
