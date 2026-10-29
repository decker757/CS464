import { describe, expect, it } from 'vitest'
import { compareCredits, formatAveragePrice, formatCredits, formatCreditsPrecise, isNegativeCredits, isPlainCredits, isZeroCredits, roundedCredits, signedAmountColor, subtractCredits, unsignedCredits } from './formatCredits'

describe('formatCredits', () => {
  it('writes a whole number of credits with no decimal part', () => {
    expect(formatCredits('1000.0000')).toBe('1,000')
  })

  it('formats four or more digits with thousands separators', () => {
    expect(formatCredits('1234567.0000')).toBe('1,234,567')
  })

  it('leaves three or fewer digits unseparated', () => {
    expect(formatCredits('250.0000')).toBe('250')
  })

  it('formats zero', () => {
    expect(formatCredits('0.0000')).toBe('0')
  })

  it('keeps the minus sign on a negative balance', () => {
    // The platform account is the only one allowed to go negative
    // (root CLAUDE.md), but the formatter is correct for any input.
    expect(formatCredits('-1000.0000')).toBe('-1,000')
  })

  it('handles a value with no decimal point', () => {
    expect(formatCredits('500')).toBe('500')
  })

  it('shows a dash for anything that is not a plain decimal', () => {
    expect(formatCredits('1E-4')).toBe('—')
  })

  it('keeps the cents of a balance that has them, rather than dropping them', () => {
    // #443, watched live through a trade: this read the integer part off the
    // string and truncated, so a balance of 9,993.8938 came out as 9,993 in
    // the navbar while the portfolio showed 9,993.8938 — the same screen, two
    // figures, 0.89 credits apart.
    expect(formatCredits('9993.8938')).toBe('9,993.89')
  })

  it.each([
    ['1.2449', '1.24', 'below the half'],
    ['1.2450', '1.25', 'exactly the half, which goes up'],
    ['1.2451', '1.25', 'above the half'],
    ['-1.2450', '-1.25', 'exactly the half on a negative, which goes away from zero'],
    ['9.9950', '10', 'a carry the whole way across the decimal point'],
    ['999.9999', '1,000', 'a carry that adds a thousands separator'],
  ])('rounds %s to %s (%s)', (value, expected) => {
    // The boundary itself, both sides of it and the carry. Truncation passes
    // the first of these and fails every other one, which is what #443 was.
    // Half away from zero is the ledger's own ROUND_HALF_UP
    // (ledger-service.md), so a figure is rounded here the way the service
    // that computed it rounds.
    expect(formatCredits(value)).toBe(expected)
  })

  it('shows an amount under a cent as a plain zero, with no sign and no minus', () => {
    // A fresh position's unrealized P&L is dust at the fourth decimal place
    // (ADR 0018: it is valued at liquidation), and "-0.00" is not a figure
    // anybody should read. The direction survives in the colour, which the
    // portfolio takes from `roundedCredits` for exactly this reason.
    expect(formatCredits('-0.0001')).toBe('0')
    expect(formatCredits('-0.0001', { showSign: true })).toBe('0')
    expect(formatCredits('0.0049', { showSign: true })).toBe('0')
  })

  it('adds a plus sign to a positive figure only when showSign is set', () => {
    // P&L is the only figure whose direction is the point, so it is the only
    // one that asks for the sign. [B-1] #376
    expect(formatCredits('3.8690')).toBe('3.87')
    expect(formatCredits('3.8690', { showSign: true })).toBe('+3.87')
    expect(formatCredits('-3.8690', { showSign: true })).toBe('-3.87')
  })
})

describe('roundedCredits', () => {
  it('rounds to the scale formatCredits shows, so a colour can be read off the figure on screen', () => {
    // #443: a portfolio card colours itself from this rather than from the
    // value behind it. A P&L of -0.0001 shows as "0", and that has to read
    // flat rather than red — which it does, because isZeroCredits reads
    // "-0.00" as zero. A loss that survives the rounding still reads as one.
    expect(roundedCredits('-0.0001')).toBe('-0.00')
    expect(signedAmountColor(roundedCredits('-0.0001'))).toBe('text-muted')
    expect(roundedCredits('-0.5000')).toBe('-0.50')
    expect(signedAmountColor(roundedCredits('-0.5000'))).toBe('text-danger')
  })

  it('gives back anything unreadable unchanged, so a caller colouring it is no worse off', () => {
    expect(roundedCredits('1E-4')).toBe('1E-4')
  })
})

describe('formatCreditsPrecise', () => {
  it('keeps all four decimal places with thousands separators', () => {
    expect(formatCreditsPrecise('1234.5678')).toBe('1,234.5678')
  })

  it('pads a shorter fractional part with zeros', () => {
    expect(formatCreditsPrecise('5.1')).toBe('5.1000')
  })

  it('rounds a fifth decimal place rather than dropping it', () => {
    // The ledger sends four places, so this is the formatter being correct
    // rather than a case anybody meets. Both formatters round the same way
    // and on the same code, which is the point of #443: one rule, not one
    // per figure.
    expect(formatCreditsPrecise('5.12349')).toBe('5.1235')
  })

  it('shows the minus sign on a negative value whether or not showSign is set', () => {
    expect(formatCreditsPrecise('-0.0313')).toBe('-0.0313')
    expect(formatCreditsPrecise('-0.0313', { showSign: true })).toBe('-0.0313')
  })

  it('adds a plus sign to a positive value only when showSign is set', () => {
    expect(formatCreditsPrecise('2.5312')).toBe('2.5312')
    expect(formatCreditsPrecise('2.5312', { showSign: true })).toBe('+2.5312')
  })

  it('shows no sign on zero even with showSign set', () => {
    // Zero is neither a gain nor a loss.
    expect(formatCreditsPrecise('0.0000', { showSign: true })).toBe('0.0000')
  })

  it('shows a dash for anything that is not a plain decimal', () => {
    expect(formatCreditsPrecise('1E-4')).toBe('—')
  })
})

describe('formatAveragePrice', () => {
  // ledger-service.md: average_price can pass 1.0000 on a heavily skewed
  // book. #247 picks credits-per-share as the one format for this field on
  // every screen, for exactly this reason. Each input below differs from its
  // expected output, so the assertion actually exercises the formatter.
  it.each([
    ['0.5', '0.5000'],
    ['2', '2.0000'],
    ['1.23456', '1.2346'],
  ])('formats %s as %s', (input, expected) => {
    expect(formatAveragePrice(input)).toBe(expected)
  })

  it('keeps all four places, and is not rounded to the cent with the cards', () => {
    // #247 and ledger-service.md: this is credits per share, so the digits
    // after the second are most of the number. #443 rounded the figures that
    // are read at a glance to the cent and deliberately left this one alone.
    expect(formatAveragePrice('0.5063')).toBe('0.5063')
  })
})

describe('unsignedCredits', () => {
  it('drops a leading minus sign', () => {
    expect(unsignedCredits('-5.1250')).toBe('5.1250')
  })

  it('leaves a positive value unchanged', () => {
    expect(unsignedCredits('5.1250')).toBe('5.1250')
  })
})

describe('compareCredits', () => {
  // The exposure page picks the worst outcome with this ([2.2] #55, #361),
  // and a sign error there understates what the platform owes. Each pair is
  // written smaller-first and checked both ways round, so an inverted
  // comparison cannot pass by being wrong twice.
  it.each([
    ['1130.0000', '1205.0000', 'two positives'],
    ['-400.0000', '80.0000', 'a negative below a positive'],
    ['-400.0000', '-18.6431', 'two negatives, the larger debt first'],
    ['-0.0001', '0.0000', 'just below zero and zero'],
    ['-0.0000', '0.0001', 'negative zero below a positive'],
    ['9.9999', '10.0000', 'a carry across the decimal point'],
    ['1.0001', '1.00019', 'fractions of different lengths'],
    ['999999999999999999.0000', '1000000000000000000.0000', 'past what a float keeps exactly'],
  ])('orders %s before %s (%s)', (smaller, larger) => {
    expect(compareCredits(smaller, larger)).toBe(-1)
    expect(compareCredits(larger, smaller)).toBe(1)
  })

  it.each([
    ['5.0000', '5.0000'],
    ['5', '5.0000'],
    ['-0.0000', '0.0000'],
    ['0005.0000', '5.00000'],
  ])('reads %s and %s as the same amount', (left, right) => {
    expect(compareCredits(left, right)).toBe(0)
  })

  it('orders anything unreadable as equal rather than throwing during render', () => {
    // BigInt throws on "1E-4"; a chart or a table that lost a page over one
    // bad figure would be worse than one that shows "—" (formatPrice.ts).
    expect(compareCredits('1E-4', '5.0000')).toBe(0)
    expect(compareCredits('5.0000', '')).toBe(0)
  })
})

describe('subtractCredits', () => {
  // The buy preview's profit is the payout minus the cost the server quoted
  // (#357), and both arrive as decimal strings. A float would get 10.0000 −
  // 5.1250 right and 0.3 − 0.1 wrong, so every pair below is one a float
  // would round or lose outright.
  it.each([
    ['10.0000', '5.1250', '4.8750', 'a buy at four decimal places'],
    ['0.3', '0.1', '0.2', 'the classic float rounding error'],
    ['10.0000', '12.5000', '-2.5000', 'a payout below the cost'],
    ['10', '2.5', '7.5', 'fractions of different lengths'],
    ['5.0000', '5.0000', '0.0000', 'nothing left over'],
    ['1000000000000000000.0001', '0.0002', '999999999999999999.9999', 'past what a float keeps exactly'],
  ])('subtracts %s − %s to %s (%s)', (left, right, expected) => {
    expect(subtractCredits(left, right)).toBe(expected)
  })

  it('subtracts a negative right-hand side by adding it', () => {
    expect(subtractCredits('5.0000', '-2.5000')).toBe('7.5000')
  })

  it('gives null for anything unreadable rather than throwing during render', () => {
    // BigInt throws on "1E-4". The caller drops the row instead, the way
    // isPlainCredits's note describes — a dash is not a credits figure, so
    // this one cannot return one.
    expect(subtractCredits('1E-4', '5.0000')).toBeNull()
    expect(subtractCredits('5.0000', '')).toBeNull()
  })
})

describe('isPlainCredits', () => {
  it('accepts a signed decimal, with or without a fraction', () => {
    expect(isPlainCredits('-400.0000')).toBe(true)
    expect(isPlainCredits('500')).toBe(true)
  })

  it('rejects what the formatters show a dash for', () => {
    expect(isPlainCredits('1E-4')).toBe(false)
    expect(isPlainCredits('')).toBe(false)
  })
})

describe('isZeroCredits', () => {
  it('is true for zero, with or without a decimal point', () => {
    expect(isZeroCredits('0.0000')).toBe(true)
    expect(isZeroCredits('0')).toBe(true)
  })

  it('is true for a negative zero', () => {
    expect(isZeroCredits('-0.0000')).toBe(true)
  })

  it('is false for any non-zero value', () => {
    expect(isZeroCredits('0.0001')).toBe(false)
    expect(isZeroCredits('-0.0001')).toBe(false)
    expect(isZeroCredits('5.0000')).toBe(false)
  })
})

describe('isNegativeCredits', () => {
  it('is true below zero and false for a negative zero, so "-0.0000" is never a shortfall', () => {
    // The exposure summary and the residue column both ask this (#377); a
    // copy of it that read the minus sign alone would have the column say
    // "tops up" beside a summary that says "keeps".
    expect(isNegativeCredits('-0.0001')).toBe(true)
    expect(isNegativeCredits('-0.0000')).toBe(false)
    expect(isNegativeCredits('0.0001')).toBe(false)
  })
})

describe('signedAmountColor', () => {
  it('keeps an exact zero neutral, so a portfolio nobody has traded reads flat', () => {
    // [B-1] #376 and ADR 0018: zero is neither a gain nor a loss, so an
    // untouched signup grant must not come out red, and a fresh position's
    // unrealized P&L must not come out green. The gain and the loss are
    // here as the contrast that makes "neutral" mean something.
    expect(signedAmountColor('0.0000')).toBe('text-muted')
    expect(signedAmountColor('-0.0000')).toBe('text-muted')
    expect(signedAmountColor('0.0001')).toBe('text-success')
    expect(signedAmountColor('-0.0001')).toBe('text-danger')
  })

  it('stays neutral, rather than throwing, on a value that is not a plain decimal', () => {
    // #436: a money field the ledger renamed or stopped sending arrives as
    // undefined, and a throw here during render took the whole portfolio down
    // to the error boundary. The formatters beside it show "—" for the same
    // input; this is the colour that goes with that dash.
    expect(signedAmountColor(undefined as unknown as string)).toBe('text-muted')
    expect(signedAmountColor('1E-4')).toBe('text-muted')
  })
})
