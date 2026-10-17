import { describe, expect, it } from 'vitest'
import { formatPrice } from './formatPrice'

// [X-1] #126 AC 3: a price is formatted from its decimal string, never through
// a float (docs/api/realtime-service.md).
describe('formatPrice', () => {
  it('shows a price as a percentage to one decimal place', () => {
    expect(formatPrice('0.6234')).toBe('62.3%')
  })

  // parseFloat('0.1235') * 100 is 12.349999…, which toFixed(1) rounds down to
  // 12.3. The ledger rounds half up everywhere, and so does this.
  it('rounds a half up on the exact digits, where a float would round it down', () => {
    expect(formatPrice('0.1235')).toBe('12.4%')
    expect(formatPrice('0.5555')).toBe('55.6%')
  })

  it('carries a rounding into the whole percent', () => {
    expect(formatPrice('0.9995')).toBe('100.0%')
  })

  it('formats the edges of the range', () => {
    expect(formatPrice('0.0000')).toBe('0.0%')
    expect(formatPrice('0.0004')).toBe('0.0%')
    expect(formatPrice('1.0000')).toBe('100.0%')
  })

  // BigInt throws on these, and a throw during render takes the page down.
  it('shows a dash for anything that is not a plain decimal', () => {
    expect(formatPrice('1E-4')).toBe('—')
  })
})
