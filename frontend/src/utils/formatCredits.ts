// Balances and amounts arrive as decimal strings, never JSON numbers
// (ledger-service.md): a JSON number is an IEEE double by the time the
// browser parses it, and a credit total off by a floating-point epsilon is a
// bug report about money. Credits are whole numbers in practice, so display
// reads the integer part directly off the string instead of parsing it as a
// float and rounding. BigInt is exact over that integer part — no float
// involved — and toLocaleString adds the thousands separators and the minus
// sign, which is what a hand-written regex was doing less readably.
export function formatCredits(balance: string): string {
  const integerPart = balance.split('.')[0] || '0'
  return BigInt(integerPart).toLocaleString('en-US')
}
