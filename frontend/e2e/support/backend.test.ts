import { describe, expect, it } from 'vitest'
import { withGuardHint } from './backend'

// #373's first acceptance criterion: a stack the suite cannot run on fails
// with a message naming the configuration. `startingGrant`'s guard covers the
// grant it can measure; this covers the one case it cannot, a spend that grew
// past LARGEST_SPEND without it being raised.

describe('withGuardHint', () => {
  it('names the guard and the setting when a buy runs out of credits', () => {
    const refusal = new Error('POST /ledger/markets/x/trades answered 409: {"error":{"code":"insufficient_funds"}}')

    const hinted = withGuardHint(refusal, '200.0000')

    expect(hinted).toBeInstanceOf(Error)
    const message = (hinted as Error).message
    expect(message).toContain('LARGEST_SPEND')
    expect(message).toContain('STARTING_CREDITS')
    expect(message).toContain('200.0000')
    // The ledger's own words are kept, so the figures it quoted are still read.
    expect(message).toContain('insufficient_funds')
  })

  it('passes every other refusal through untouched', () => {
    // close-market.e2e.ts matches `market_closed` on this exact message, so a
    // hint added to it would break that spec in a full run and nowhere else.
    const refusal = new Error('POST /ledger/markets/x/trades answered 409: {"error":{"code":"market_closed"}}')

    expect(withGuardHint(refusal, '1')).toBe(refusal)
  })

  it('passes a thrown non-Error through rather than losing it', () => {
    expect(withGuardHint('socket hang up', '1')).toBe('socket hang up')
  })
})
