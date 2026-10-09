import { describe, expect, it } from 'vitest'
import { formatCredits, formatCreditsPrecise, isZeroCredits, unsignedCredits } from './formatCredits'

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

describe('unsignedCredits', () => {
  it('drops a leading minus sign', () => {
    expect(unsignedCredits('-5.1250')).toBe('5.1250')
  })

  it('leaves a positive value unchanged', () => {
    expect(unsignedCredits('5.1250')).toBe('5.1250')
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
