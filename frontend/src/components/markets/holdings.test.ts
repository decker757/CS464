import { describe, expect, it } from 'vitest'
import type { Position } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { heldOutcomesOf } from './holdings'

const OUTCOMES: PublicOutcome[] = [
  { id: 'm1-0', position: 0, label: 'Yes' },
  { id: 'm1-1', position: 1, label: 'No' },
]

function position(outcomeId: string, quantity: string, overrides: Partial<Position> = {}): Position {
  return {
    market_id: 'm1',
    outcome_id: outcomeId,
    outcome_position: 0,
    quantity,
    reserved_shares: '0.0000',
    cost_basis: '10.0000',
    average_entry_price: '0.5000',
    price: '0.6000',
    value: '12.0000',
    unrealized_pnl: '2.0000',
    result: null,
    payout: null,
    state_version: 3,
    ...overrides,
  }
}

describe('heldOutcomesOf', () => {
  // [X-1] #450 AC 1: the card says they hold it, and on which side.
  it('names each held outcome with its quantity', () => {
    const held = heldOutcomesOf([position('m1-0', '40.0000')], OUTCOMES)

    expect(held).toEqual([{ outcomeId: 'm1-0', label: 'Yes', quantity: '40.0000' }])
  })

  // AC 4: a market held on more than one outcome renders sensibly. On a
  // binary book holding both sides is the ordinary case, not an oddity.
  it('names every held outcome, in the market order', () => {
    const held = heldOutcomesOf(
      // Deliberately out of the market's order.
      [position('m1-1', '12.0000'), position('m1-0', '40.0000')],
      OUTCOMES,
    )

    expect(held.map(entry => entry.label)).toEqual(['Yes', 'No'])
  })

  // AC 2: a market they do not hold is unchanged — no empty slot or dash. The
  // card renders nothing at all from an empty list.
  it('holds nothing for a market with no positions', () => {
    expect(heldOutcomesOf(undefined, OUTCOMES)).toEqual([])
    expect(heldOutcomesOf([], OUTCOMES)).toEqual([])
  })

  // An outcome the trader has no row for is not a holding of zero.
  it('leaves out an outcome with no position', () => {
    const held = heldOutcomesOf([position('m1-0', '40.0000')], OUTCOMES)

    expect(held.map(entry => entry.label)).toEqual(['Yes'])
  })

  // The ledger keeps a settled market's rows to report what they were paid
  // (ledger-service.md), but the shares are gone — the market paid them out.
  // "You hold 40 Yes" there would be the card's own lie.
  it('leaves out a settled market, which no longer holds anything', () => {
    const paidOut = heldOutcomesOf([position('m1-0', '40.0000', { result: 'paid_out', payout: '40.0000' })], OUTCOMES)
    const worthless = heldOutcomesOf([position('m1-1', '12.0000', { result: 'worthless', payout: '0.0000' })], OUTCOMES)

    expect(paidOut).toEqual([])
    expect(worthless).toEqual([])
  })

  // The ledger carries ids and the labels are the frontend's own join, so an
  // outcome the market no longer lists has no name to show.
  it('leaves out a position whose outcome the market no longer lists', () => {
    const held = heldOutcomesOf([position('gone', '40.0000')], OUTCOMES)

    expect(held).toEqual([])
  })
})
