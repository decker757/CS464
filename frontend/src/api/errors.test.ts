import { describe, expect, it } from 'vitest'
import { AxiosError, type AxiosResponse } from 'axios'
import { GENERIC_ERROR, describeInsufficientFunds, describeInsufficientShares, describeOrderError, describeTradeError } from './errors'

/** A refusal from the ledger's trade route, the shape describeTradeError
 *  reads: a 409 unless a test says otherwise. `details` is a list only for
 *  invalid_request, as pydantic's entries, which arrives as a 422. */
function ledgerRefusal(
  code: string,
  details: Record<string, string> | { loc: string[]; msg: string; type: string }[],
  status = 409,
): AxiosError {
  const response = {
    status,
    data: { error: { code, message: 'Refused.', details } },
  } as AxiosResponse
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', undefined, undefined, response)
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

describe('describeOrderError', () => {
  it('names the bound a crossing buy has to stay below, in the units the limit field takes', () => {
    // ADR 0024 keeps the book one-sided, and the trader's own resting orders
    // count towards it — so the message has to name the price, not just say
    // "in the way".
    const message = describeOrderError(
      ledgerRefusal('order_crosses_book', { side: 'buy', max_limit_price: '0.7000' }),
    )
    // The field takes 0.70, so the bound reads 0.7000 rather than 70.0%.
    expect(message).toContain('below 0.7000')
  })

  it('names the bound a crossing sell has to stay above', () => {
    const message = describeOrderError(
      ledgerRefusal('order_crosses_book', { side: 'sell', min_limit_price: '0.3000' }),
    )
    expect(message).toContain('above 0.3000')
  })

  it('reads the shortfall, not the trade route\'s required, on a buy that does not fit', () => {
    // The placement's insufficient_funds carries `shortfall` and no
    // `required`: the whole budget must fit in `available` even when the
    // order would fill for less (ledger-service.md).
    const message = describeOrderError(
      ledgerRefusal('insufficient_funds', {
        balance: '994.8750', reserved: '15.0000', available: '979.8750', shortfall: '3970.1250',
      }),
    )
    expect(message).toContain('979.8750')
    expect(message).toContain('3,970.1250')
  })

  it('tells a trader their limit order is not available on a multi-outcome market', () => {
    expect(describeOrderError(ledgerRefusal('market_not_binary', {}))).toMatch(/two outcomes/i)
  })

  it.each([
    ['order_already_filled', /filled before the cancel/i],
    ['order_already_cancelled', /already cancelled/i],
    ['order_released', /already released/i],
    ['order_not_found', /no longer there/i],
  ])('explains the %s refusal a cancel can get', (code, expected) => {
    expect(describeOrderError(ledgerRefusal(code, {}))).toMatch(expected)
  })

  it('words a sell order\'s insufficient_shares_held the way a trade\'s is worded', () => {
    // #411's sentence, reached through the trade table rather than a second
    // copy of the branch.
    const err = ledgerRefusal('insufficient_shares_held', { held: '6.0000', requested: '10.0000', reserved: '2.0000' })

    expect(describeOrderError(err)).toBe(
      'You have 4.0000 shares available to sell, but this sell asks for 10.0000. '
      + 'You hold 6.0000 in all, and 2.0000 of them are reserved by your open sell orders.',
    )
  })

  it('falls back to the trade wording for a code both routes share', () => {
    // unknown_outcome means the same thing on a placement as on a trade, so
    // there is deliberately no second copy of its sentence.
    expect(describeOrderError(ledgerRefusal('unknown_outcome', {}))).toMatch(/no longer part of this market/i)
  })

  it('names the limit price when an invalid_request is about it', () => {
    // The trade table's invalid_request sentence is about a quantity, which
    // would send the trader to the wrong field (ledger-service.md: a limit
    // price at or outside 0 and 1, or at five places, is refused this way).
    const err = ledgerRefusal('invalid_request', [
      { loc: ['body', 'limit_price'], msg: 'Input should be less than 1', type: 'less_than' },
    ], 422)
    expect(describeOrderError(err)).toBe("That limit price isn't valid.")
  })

  it('names no field when an invalid_request is about something else', () => {
    const err = ledgerRefusal('invalid_request', [
      { loc: ['body', 'side'], msg: "Input should be 'buy' or 'sell'", type: 'literal_error' },
    ], 422)
    expect(describeOrderError(err)).toBe("Some of this order's details aren't valid.")
  })

  it('falls back to the generic message for a code it has never seen', () => {
    expect(describeOrderError(ledgerRefusal('something_new_entirely', {}))).toBe(GENERIC_ERROR)
  })

  it('falls back to the generic message for a failure that is not an API error', () => {
    expect(describeOrderError(new Error('network down'))).toBe(GENERIC_ERROR)
  })
})
