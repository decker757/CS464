import { describe, expect, it } from 'vitest'
import { movementLabel, priceMovementOf } from './priceMovement'

describe('priceMovementOf', () => {
  // [X-1] #444 AC 3: a price that changes is marked as having changed. The
  // direction is what decides the cue, so it has to be the direction and not
  // merely "something happened".
  it('reports which way the price went, and nothing when it held', () => {
    expect(priceMovementOf('0.5000', '0.6000')).toBe('up')
    expect(priceMovementOf('0.6000', '0.5000')).toBe('down')
    expect(priceMovementOf('0.5000', '0.5000')).toBeNull()
  })

  // A price is a decimal string and two of them can differ in trailing zeros
  // without the number differing. A string comparison would call that a move
  // and flash a card nobody traded in.
  it('compares the numbers, not the strings', () => {
    expect(priceMovementOf('0.5000', '0.50')).toBeNull()
  })

  // The ledger sends decimal strings; anything else is a bug upstream, and a
  // cue is not worth a crash on a card (#436's rule for signedAmountColor).
  it('says nothing about a value that is not a number', () => {
    expect(priceMovementOf('0.5000', 'n/a')).toBeNull()
    expect(priceMovementOf('', '0.5000')).toBeNull()
  })
})

describe('movementLabel', () => {
  // The cue must not be colour alone (AC 3), so it is also said in words.
  it('says which outcome moved and which way', () => {
    expect(movementLabel('Yes', 'up')).toBe('Yes price rose')
    expect(movementLabel('No', 'down')).toBe('No price fell')
  })
})
