import { describe, expect, it } from 'vitest'
import { formatCredits } from './formatCredits'

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
