import { describe, expect, it } from 'vitest'
import {
  addCredits,
  formatPreciseCredits,
  formatSignedCredits,
  formatWholeCredits,
  isBelow,
  subtractCredits,
} from './credits'

// #373. The end-to-end specs work their expected figures out from the grant
// the ledger reports, through these, so a mistake here would make every one of
// them expect the wrong thing. Tested on their own, with the answers typed out.

describe('subtractCredits', () => {
  it('takes a trade off the grant exactly, borrowing across the decimal point', () => {
    expect(subtractCredits('1234.5678', '8.8592')).toBe('1225.7086')
  })

  it('reads an amount with fewer than four places as if padded', () => {
    expect(subtractCredits('2500', '0.0001')).toBe('2499.9999')
  })

  it('refuses to go below zero rather than return a figure no screen can show', () => {
    expect(() => subtractCredits('100.0000', '143.3781')).toThrow(/below zero/)
  })
})

describe('addCredits', () => {
  it('adds exactly, carrying nothing it should not', () => {
    expect(addCredits('1234.5678', '2000')).toBe('3234.5678')
  })
})

describe('isBelow', () => {
  it('is true one ten-thousandth under the floor and false on it', () => {
    expect(isBelow('143.3780', '143.3781')).toBe(true)
    expect(isBelow('143.3781', '143.3781')).toBe(false)
  })

  it('compares amounts, not strings', () => {
    expect(isBelow('1000', '143.3781')).toBe(false)
  })
})

describe('reading an amount', () => {
  it('refuses anything but a plain decimal of at most four places', () => {
    expect(() => subtractCredits('1.23456', '0')).toThrow(/not a credits amount/)
    expect(() => subtractCredits('1e3', '0')).toThrow(/not a credits amount/)
    expect(() => subtractCredits('1,000.0000', '0')).toThrow(/not a credits amount/)
  })
})

describe('formatWholeCredits', () => {
  it('drops the fraction as the navbar does, rather than rounding it', () => {
    expect(formatWholeCredits('999.9999')).toBe('999')
  })

  it('groups the thousands with commas', () => {
    expect(formatWholeCredits('1234567.8900')).toBe('1,234,567')
  })
})

describe('formatPreciseCredits', () => {
  it('shows four places, grouped', () => {
    expect(formatPreciseCredits('1234567.8')).toBe('1,234,567.8000')
  })
})

describe('formatSignedCredits', () => {
  it('marks money paid in with a plus, as the history shows the grant', () => {
    expect(formatSignedCredits('1000.0000')).toBe('+1,000.0000')
  })
})
