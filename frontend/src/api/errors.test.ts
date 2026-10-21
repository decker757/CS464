import { describe, expect, it } from 'vitest'
import { AxiosError, type AxiosResponse } from 'axios'
import { describeInsufficientFunds, describeInsufficientShares, describeTradeError } from './errors'

/** A 409 from the ledger's trade route, the shape describeTradeError reads. */
function ledgerRefusal(code: string, details: Record<string, string>): AxiosError {
  const response = {
    status: 409,
    data: { error: { code, message: 'Refused.', details } },
  } as AxiosResponse
  return new AxiosError('Request failed with status code 409', 'ERR_BAD_REQUEST', undefined, undefined, response)
}

// #411: the ledger refuses a buy on `available` and a sell on the position
// less what the trader's own orders reserve (ledger-service.md), so the
// message has to name that figure and say where the rest went — and must not
// mention orders when there are none.
describe('describeTradeError on the two refusals that name figures', () => {
  it('names the available credits, the balance and what the open orders hold', () => {
    const err = ledgerRefusal('insufficient_funds', {
      balance: '1000.0000',
      required: '500.0000',
      reserved: '600.0000',
      available: '400.0000',
    })

    expect(describeTradeError(err)).toBe(
      'You have 400.0000 credits available, but this trade needs 500.0000. '
      + 'Your balance is 1,000.0000, and 600.0000 of it is held by your open buy orders.',
    )
  })

  it('names the balance alone, and no orders at all, when nothing is reserved', () => {
    const err = ledgerRefusal('insufficient_funds', {
      balance: '1000.0000',
      required: '1500.0000',
      reserved: '0.0000',
      available: '1000.0000',
    })

    expect(describeTradeError(err)).toBe('You have 1,000.0000 credits, but this trade needs 1,500.0000.')
    expect(describeTradeError(err)).not.toMatch(/order/i)
  })

  it('names the shares left to sell, the whole position and what the open orders reserve', () => {
    const err = ledgerRefusal('insufficient_shares_held', {
      held: '6.0000',
      requested: '10.0000',
      reserved: '2.0000',
    })

    expect(describeTradeError(err)).toBe(
      'You have 4.0000 shares available to sell, but this sell asks for 10.0000. '
      + 'You hold 6.0000 in all, and 2.0000 of them are reserved by your open sell orders.',
    )
  })

  it('names the holding alone, and no orders at all, when nothing is reserved', () => {
    const err = ledgerRefusal('insufficient_shares_held', {
      held: '6.0000',
      requested: '10.0000',
      reserved: '0.0000',
    })

    expect(describeTradeError(err)).toBe('You hold 6.0000 shares, but this sell asks for 10.0000.')
    expect(describeTradeError(err)).not.toMatch(/order/i)
  })

  // The trading panel words its own pre-send check with these two functions
  // (#357) and holds no reservation figure to pass, so both must still answer
  // on two arguments — and answer without mentioning orders.
  it('keeps the plain wording for the caller that has no reservation figures', () => {
    expect(describeInsufficientFunds('1000.0000', '1500.0000')).toBe(
      'You have 1,000.0000 credits, but this trade needs 1,500.0000.',
    )
    expect(describeInsufficientShares('6.0000', '10.0000')).toBe(
      'You hold 6.0000 shares, but this sell asks for 10.0000.',
    )
  })
})
