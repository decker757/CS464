import { describe, expect, it } from 'vitest'
import { compareCredits, formatAveragePrice, formatCredits, formatCreditsPrecise, isPlainCredits, isZeroCredits, unsignedCredits } from './formatCredits'

describe('formatCredits', () => {
  it('drops the decimal part', () => {
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

  it('does not round a fractional part up', () => {
    // Credits are whole numbers in practice (ledger-service.md); display
    // truncates rather than rounds, so 999.9999 reads as 999, not 1,000.
    expect(formatCredits('999.9999')).toBe('999')
  })

  it('handles a value with no decimal point', () => {
    expect(formatCredits('500')).toBe('500')
  })

  it('shows a dash for anything that is not a plain decimal', () => {
    expect(formatCredits('1E-4')).toBe('—')
  })
})

describe('formatCreditsPrecise', () => {
  it('keeps all four decimal places with thousands separators', () => {
    expect(formatCreditsPrecise('1234.5678')).toBe('1,234.5678')
  })

  it('pads a shorter fractional part with zeros', () => {
    expect(formatCreditsPrecise('5.1')).toBe('5.1000')
  })

  it('does not round a fifth decimal place; it truncates', () => {
    expect(formatCreditsPrecise('5.12349')).toBe('5.1234')
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
    ['1.23456', '1.2345'],
  ])('formats %s as %s', (input, expected) => {
    expect(formatAveragePrice(input)).toBe(expected)
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
