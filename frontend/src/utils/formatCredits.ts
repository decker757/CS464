// Balances and amounts arrive as decimal strings, never JSON numbers
// (ledger-service.md): a JSON number is an IEEE double by the time the
// browser parses it, and a credit total off by a floating-point epsilon is a
// bug report about money. Credits are whole numbers in practice, so display
// reads the integer part directly off the string instead of parsing it as a
// float and rounding. BigInt is exact over that integer part — no float
// involved — and toLocaleString adds the thousands separators and the minus
// sign.
//
// Anything that is not a plain signed decimal shows "—" rather than letting
// BigInt throw during render and take the whole page down (formatPrice.ts).
const PLAIN_SIGNED_DECIMAL = /^-?\d+(\.\d+)?$/

export function formatCredits(balance: string): string {
  if (!PLAIN_SIGNED_DECIMAL.test(balance)) return '—'

  const integerPart = balance.split('.')[0] || '0'
  return BigInt(integerPart).toLocaleString('en-US')
}
