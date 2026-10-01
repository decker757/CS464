// Balances and amounts arrive as decimal strings, never JSON numbers
// (ledger-service.md): a JSON number is an IEEE double by the time the
// browser parses it, and a credit total off by a floating-point epsilon is a
// bug report about money. Credits are whole numbers in practice, so display
// reads the integer part directly off the string instead of parsing it as a
// float and rounding.
export function formatCredits(balance: string): string {
  const [whole, isNegative] = balance.startsWith('-') ? [balance.slice(1), true] : [balance, false]
  const integerPart = whole.split('.')[0] || '0'
  const withSeparators = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return isNegative ? `-${withSeparators}` : withSeparators
}
