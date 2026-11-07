import { describe, expect, it } from 'vitest'
import { priceLimitFor } from './priceLimit'

// #644: the price limit a trade sends, 1% either side of the quoted total.
describe('priceLimitFor', () => {
  it('lets a buy pay up to 1% more than quoted, rounded up to the tick', () => {
    // 5.1250 × 1.01 = 5.17625, which rounds up to 5.1763. A buy's total
    // arrives negative (credits out); the limit is its magnitude.
    expect(priceLimitFor('buy', '-5.1250')).toBe('5.1763')
    // 1.2345 × 1.01 = 1.246845: up gives 1.2469, where rounding to the
    // nearest tick would give 1.2468.
    expect(priceLimitFor('buy', '-1.2345')).toBe('1.2469')
  })

  it('lets a sell receive down to 1% less than quoted, rounded down to the tick', () => {
    // 4.9876 × 0.99 = 4.937724, which rounds down to 4.9377.
    expect(priceLimitFor('sell', '4.9876')).toBe('4.9377')
    // 1.0101 × 0.99 = 0.999999: down gives 0.9999, where rounding to the
    // nearest tick would give 1.0000.
    expect(priceLimitFor('sell', '1.0101')).toBe('0.9999')
  })

  it('does not round a limit that lands exactly on a tick', () => {
    expect(priceLimitFor('buy', '-10.0000')).toBe('10.1000')
    expect(priceLimitFor('sell', '10.0000')).toBe('9.9000')
  })

  it('keeps four places on a limit below one credit', () => {
    // 0.0099 × 1.01 = 0.009999, up to 0.0100; × 0.99 = 0.009801, down to 0.0098.
    expect(priceLimitFor('buy', '-0.0099')).toBe('0.0100')
    expect(priceLimitFor('sell', '0.0099')).toBe('0.0098')
  })

  it('gives no limit for a total that is not a plain decimal, rather than a wrong one', () => {
    expect(priceLimitFor('buy', 'not-a-number')).toBeNull()
  })
})
