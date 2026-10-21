import { describe, expect, it } from 'vitest'
import {
  creditsLess,
  creditsPlus,
  formatPreciseCredits,
  formatRoundedCredits,
  formatSignedCredits,
  isBelow,
} from './credits'

// #373. The end-to-end specs work their expected figures out from the grant
// the ledger reports, through these, so a mistake here would make every one of
// them expect the wrong thing. Tested on their own, with the answers typed out.

describe('creditsLess', () => {
  it('takes a trade off the grant exactly, borrowing across the decimal point', () => {
    expect(creditsLess('1234.5678', '8.8592')).toBe('1225.7086')
  })

  it('reads an amount with fewer than four places as if padded', () => {
    expect(creditsLess('2500.5', '0.0001')).toBe('2500.4999')
  })

  it('refuses to go below zero rather than return a figure no screen can show', () => {
    expect(() => creditsLess('100.0000', '143.3781')).toThrow(/below zero/)
  })
})

describe('creditsPlus', () => {
  it('adds exactly, carrying across the decimal point', () => {
    expect(creditsPlus('0.9999', '0.0001')).toBe('1.0000')
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
    expect(() => creditsLess('1.23456', '0')).toThrow(/not a credits amount/)
    expect(() => creditsLess('1e3', '0')).toThrow(/not a credits amount/)
    expect(() => creditsLess('1,000.0000', '0')).toThrow(/not a credits amount/)
  })
})

describe('formatRoundedCredits', () => {
  // #443: the navbar and the portfolio's cards round to the cent. They used
  // to drop the fraction, so 999.9999 read as 999 — a figure 0.9999 credits
  // short of the one the same screen showed elsewhere.
  it.each([
    ['999.9999', '1,000', 'a carry the whole way across the decimal point'],
    ['1234.5678', '1,234.57', 'the grant the end-to-end run is given'],
    ['1234.5649', '1,234.56', 'just below the half'],
    ['1234.5650', '1,234.57', 'exactly the half, which goes up'],
    ['1234567.8900', '1,234,567.89', 'thousands grouped with commas'],
    ['1234.0000', '1,234', 'a whole number of credits, with no decimal part'],
    ['0.0049', '0', 'less than half a cent, which is no cents at all'],
  ])('shows %s as %s (%s)', (amount, expected) => {
    expect(formatRoundedCredits(amount)).toBe(expected)
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

  it('leaves zero unsigned, as the app does', () => {
    expect(formatSignedCredits('0.0000')).toBe('0.0000')
  })
})
