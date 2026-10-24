import { describe, expect, it } from 'vitest'
import { limitPriceError, quantityError } from './orderInput'

describe('quantityError', () => {
  it('accepts a quantity the ledger accepts', () => {
    expect(quantityError('10')).toBeUndefined()
    expect(quantityError('0.0001')).toBeUndefined()
  })

  it('says nothing about an empty field', () => {
    // The submit button refuses an empty field; an error under one nobody
    // has typed in yet would be noise.
    expect(quantityError('')).toBeUndefined()
    expect(quantityError('  ')).toBeUndefined()
  })

  it.each([
    ['abc', /number/i],
    ['-5', /number/i],
    ['0', /greater than zero/i],
    ['0.00000', /greater than zero/i],
    ['1.00001', /4 decimal places/i],
  ])('refuses %s before it reaches the ledger', (raw, expected) => {
    expect(quantityError(raw)).toMatch(expected)
  })

  it('refuses more digits than the ledger can store', () => {
    // D-038 caps it at 18 digits in all, so the ledger never answers
    // quantity_too_large for something the field could have caught.
    expect(quantityError('1'.repeat(19))).toMatch(/too large/i)
    expect(quantityError('1'.repeat(18))).toBeUndefined()
  })
})

describe('limitPriceError', () => {
  it('accepts the ends of the range the ledger takes', () => {
    expect(limitPriceError('0.0001')).toBeUndefined()
    expect(limitPriceError('0.9999')).toBeUndefined()
    expect(limitPriceError('0.5')).toBeUndefined()
  })

  it('says nothing about an empty field', () => {
    expect(limitPriceError('')).toBeUndefined()
  })

  it.each([
    ['0', /above 0/i],
    ['0.0000', /above 0/i],
    ['1', /below 1/i],
    ['1.0000', /below 1/i],
    ['2.5', /below 1/i],
    ['0.12345', /4 decimal places/i],
    ['abc', /number/i],
    ['-0.5', /number/i],
  ])('refuses %s, which the ledger would answer invalid_request for', (raw, expected) => {
    expect(limitPriceError(raw)).toMatch(expected)
  })
})
