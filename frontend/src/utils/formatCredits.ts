// Balances and amounts arrive as decimal strings, never JSON numbers
// (ledger-service.md): a JSON number is an IEEE double by the time the
// browser parses it, and a credit total off by a floating-point epsilon is a
// bug report about money. Every formatter here works on the string's own
// digits; none of them parseFloat it.

function splitSign(value: string): { digits: string; isNegative: boolean } {
  return value.startsWith('-') ? { digits: value.slice(1), isNegative: true } : { digits: value, isNegative: false }
}

/** The magnitude of a decimal string, sign dropped — for a cost or proceeds
 * display where the direction is already said in words ("Cost", "Bought"). */
export function unsignedCredits(value: string): string {
  return splitSign(value).digits
}

function withThousandsSeparators(integerPart: string): string {
  return integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

// Credits are whole numbers in practice, so display reads the integer part
// directly off the string instead of parsing it as a float and rounding.
export function formatCredits(balance: string): string {
  const { digits, isNegative } = splitSign(balance)
  const integerPart = withThousandsSeparators(digits.split('.')[0] || '0')
  return isNegative ? `-${integerPart}` : integerPart
}

// For a value where the decimal places matter — a trade's exact cost, a
// position's cost basis or P&L — rather than a balance read as a whole
// number. Keeps exactly 4 decimal places, padding or truncating the string
// as written rather than rounding a parsed float.
export function formatCreditsPrecise(value: string, { showSign = false } = {}): string {
  const { digits, isNegative } = splitSign(value)
  const [wholeRaw, fractionRaw = ''] = digits.split('.')
  const integerPart = withThousandsSeparators(wholeRaw || '0')
  const fractionPart = (fractionRaw + '0000').slice(0, 4)
  const isZero = /^0*$/.test(wholeRaw) && /^0*$/.test(fractionRaw)
  const sign = isNegative ? '-' : (showSign && !isZero) ? '+' : ''
  return `${sign}${integerPart}.${fractionPart}`
}
