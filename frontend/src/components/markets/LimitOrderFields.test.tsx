import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../../test/server'
import LimitOrderFields from './LimitOrderFields'

const LEDGER_BASE = 'http://localhost:8003'
const MARKET_ID = 'mkt-1'
const ORDERS = `${LEDGER_BASE}/ledger/markets/${MARKET_ID}/orders`

// The live ledger's own response, from a placement against a running stack.
function orderResponse(overrides: Record<string, unknown> = {}, fills: unknown[] = []) {
  return {
    order: {
      id: 'ord-1',
      market_id: MARKET_ID,
      outcome_id: 'o-yes',
      outcome_position: 0,
      side: 'buy',
      limit_price: '0.3000',
      quantity: '50.0000',
      filled_quantity: '0.0000',
      average_fill_price: null,
      reserved_credits: '15.0000',
      reserved_shares: null,
      status: 'open',
      created_at: '2026-10-20T15:52:10.744443Z',
      cancelled_at: null,
      ...overrides,
    },
    fills,
    book_state_version: 0,
  }
}

interface FieldsOptions {
  side?: 'buy' | 'sell'
  outcomeId?: string
  outcomeLabel?: string
  heldQuantity?: string | null
  sellableQuantity?: string | null
  holdsNoneToSell?: boolean
  onBuyInstead?: () => void
  onPlaced?: () => void
}

function fieldsFor({
  side = 'buy', outcomeId = 'o-yes', outcomeLabel = 'Yes', heldQuantity = '100.0000', sellableQuantity = heldQuantity,
  holdsNoneToSell = false, onBuyInstead = vi.fn(), onPlaced = vi.fn(),
}: FieldsOptions = {}) {
  return (
    <LimitOrderFields
      marketId={MARKET_ID}
      side={side}
      outcomeId={outcomeId}
      outcomeLabel={outcomeLabel}
      holdsNoneToSell={holdsNoneToSell}
      onBuyInstead={onBuyInstead}
      heldQuantity={heldQuantity}
      sellableQuantity={sellableQuantity}
      onPlaced={onPlaced}
    />
  )
}

function renderFields(options: FieldsOptions = {}) {
  const onPlaced = options.onPlaced ?? vi.fn()
  const { rerender } = render(fieldsFor({ ...options, onPlaced }))
  // The panel above chooses the side and the outcome, so a test changes them
  // the way the panel does: as new props on the same mounted fields.
  const rerenderWith = (changes: FieldsOptions) => rerender(fieldsFor({ ...options, onPlaced, ...changes }))
  return { actor: userEvent.setup(), onPlaced, rerenderWith }
}

async function fillIn(actor: ReturnType<typeof userEvent.setup>, price: string, quantity: string) {
  await actor.type(screen.getByLabelText('Limit price'), price)
  await actor.type(screen.getByLabelText('Quantity (shares)'), quantity)
}

describe('LimitOrderFields', () => {
  it('sends the limit price and quantity with a generated idempotency key, and no state_version', async () => {
    // ADR 0024: the limit is the protection, so the ledger takes no quote —
    // sending one would refuse the order for the move it was placed to catch.
    let sentBody: Record<string, unknown> | undefined
    server.use(http.post(ORDERS, async ({ request }) => {
      sentBody = await request.json() as Record<string, unknown>
      return HttpResponse.json(orderResponse(), { status: 201 })
    }))
    const { actor } = renderFields()

    await fillIn(actor, '0.3', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    await waitFor(() => expect(sentBody).toBeDefined())
    expect(sentBody).toMatchObject({ outcome_id: 'o-yes', side: 'buy', limit_price: '0.3', quantity: '50' })
    expect(typeof sentBody?.idempotency_key).toBe('string')
    expect(sentBody).not.toHaveProperty('state_version')
  })

  it('says the order is resting when nothing filled on arrival', async () => {
    server.use(http.post(ORDERS, () => HttpResponse.json(orderResponse(), { status: 201 })))
    const { actor, onPlaced } = renderFields()

    await fillIn(actor, '0.3', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    // The limit in the units it was typed in.
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Buy 50.0000 Yes at 0.3000: resting until the price reaches it.'))
    expect(onPlaced).toHaveBeenCalled()
  })

  it('says it filled in full when the placement was marketable on arrival', async () => {
    server.use(http.post(ORDERS, () => HttpResponse.json(
      orderResponse(
        { limit_price: '0.9000', quantity: '10.0000', filled_quantity: '10.0000', average_fill_price: '0.5125', reserved_credits: '0.0000', status: 'filled' },
        [{ transaction_id: 't1', quantity: '10.0000', total: '-5.1250', state_version: 1 }],
      ),
      { status: 201 },
    )))
    const { actor } = renderFields()

    await fillIn(actor, '0.9', '10')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    // The average fill price, not the limit: what it actually cost. Credits
    // per share, never a percentage (#247): it can pass 1.0000 on a skewed
    // book, which a percentage cannot show.
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('filled in full at 0.5125 credits/share.'))
  })

  it('says how much filled when only part of the order did', async () => {
    server.use(http.post(ORDERS, () => HttpResponse.json(
      orderResponse({ filled_quantity: '20.5465', average_fill_price: '0.5750', reserved_credits: '48.1848' }),
      { status: 201 },
    )))
    const { actor } = renderFields()

    await fillIn(actor, '0.6', '100')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/20\.5465 filled, the rest is resting/i))
  })

  it('refuses to send a limit price the ledger would answer invalid_request for', async () => {
    let calls = 0
    server.use(http.post(ORDERS, () => { calls += 1; return HttpResponse.json(orderResponse(), { status: 201 }) }))
    const { actor } = renderFields()

    await fillIn(actor, '1.5', '50')

    expect(screen.getByRole('alert')).toHaveTextContent(/below 1/i)
    expect(screen.getByRole('button', { name: /place buy order/i })).toBeDisabled()
    expect(calls).toBe(0)
  })

  it('keeps the button disabled until both fields are filled', async () => {
    const { actor } = renderFields()
    const place = screen.getByRole('button', { name: /place buy order/i })
    expect(place).toBeDisabled()

    await actor.type(screen.getByLabelText('Limit price'), '0.3')
    expect(place).toBeDisabled()

    await actor.type(screen.getByLabelText('Quantity (shares)'), '50')
    expect(place).toBeEnabled()
  })

  it('names the bound when the order would cross a resting one', async () => {
    server.use(http.post(ORDERS, () => HttpResponse.json(
      { error: { code: 'order_crosses_book', message: 'in the way', details: { side: 'buy', max_limit_price: '0.7000' } } },
      { status: 409 },
    )))
    const { actor, onPlaced } = renderFields()

    await fillIn(actor, '0.8', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    // In the units the field takes, not a percentage of them.
    expect(await screen.findByRole('alert')).toHaveTextContent("a buy's limit must be below 0.7000.")
    // The order in the way may be one of the trader's own, so the list is
    // worth re-reading even though nothing was placed.
    expect(onPlaced).toHaveBeenCalled()
  })

  it('shows the shortfall when the budget does not fit', async () => {
    server.use(http.post(ORDERS, () => HttpResponse.json(
      { error: { code: 'insufficient_funds', message: 'no', details: { balance: '994.8750', reserved: '15.0000', available: '979.8750', shortfall: '3970.1250' } } },
      { status: 409 },
    )))
    const { actor } = renderFields()

    await fillIn(actor, '0.99', '5000')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/3,970\.1250 short/i)
  })

  // [T-6] #496's first criterion: the limit ticket makes the same offer the
  // market ticket does. Before this, a trader could choose Sell on an outcome
  // they hold none of, fill in a price and a quantity, and learn only from the
  // ledger's refusal that it could never work.
  it('says so and offers to buy when the trader holds none of the outcome', async () => {
    renderFields({ side: 'sell', outcomeLabel: 'No', heldQuantity: '0.0000', holdsNoneToSell: true })

    expect(screen.getByRole('status')).toHaveTextContent("You don't hold any No shares.")
    expect(screen.getByRole('button', { name: 'Buy No instead' })).toBeInTheDocument()
    // The form it replaces: a price and a quantity for shares the trader does
    // not hold lead only to that refusal.
    expect(screen.queryByLabelText('Limit price')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Quantity (shares)')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /place sell order/i })).not.toBeInTheDocument()
  })

  // The region is mounted before the sentence arrives in it: a screen reader
  // announces a live region whose content changes, and may not announce one
  // that appears with its content already there (#478's rule, now both
  // tickets').
  it('keeps the live region mounted and empty while the trader holds some', async () => {
    const { rerenderWith } = renderFields({ side: 'sell', outcomeLabel: 'No', heldQuantity: '100.0000' })

    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    rerenderWith({ side: 'sell', outcomeLabel: 'No', heldQuantity: '0.0000', holdsNoneToSell: true })

    expect(screen.getByRole('status')).toHaveTextContent("You don't hold any No shares.")
  })

  it('drops the placed line once the trader turns to the other side', async () => {
    // The line names a side and an outcome, so on the other side it would
    // describe an order the ticket is no longer set up for.
    server.use(http.post(ORDERS, () => HttpResponse.json(orderResponse(), { status: 201 })))
    const { actor, rerenderWith } = renderFields()

    await fillIn(actor, '0.3', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/resting until the price reaches it/i))

    rerenderWith({ side: 'sell' })

    // The ticket's one live region stays on the page ([T-6] #496), so it is
    // asked to be empty rather than to be gone: no placed line of any kind.
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
  })

  it('drops a refusal once the trader picks the other outcome', async () => {
    server.use(http.post(ORDERS, () => HttpResponse.json(
      { error: { code: 'order_crosses_book', message: 'in the way', details: { side: 'buy', max_limit_price: '0.7000' } } },
      { status: 409 },
    )))
    const { actor, rerenderWith } = renderFields()

    await fillIn(actor, '0.8', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))
    await screen.findByRole('alert')

    rerenderWith({ outcomeId: 'o-no', outcomeLabel: 'No' })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the holding on a sell, so the trader knows what they can offer', () => {
    renderFields({ side: 'sell', heldQuantity: '42.0000' })
    expect(screen.getByLabelText('shares held')).toHaveTextContent('42.0000')
  })

  it('shows what is left to sell when open sell orders reserve part of the holding', () => {
    // A second sell order can only offer what the first does not hold back
    // (ledger-service.md's insufficient_shares_held).
    renderFields({ side: 'sell', heldQuantity: '42.0000', sellableQuantity: '30.0000' })
    expect(screen.getByLabelText('shares held')).toHaveTextContent('You can sell 30.0000 of your 42.0000 shares')
  })

  it('shows no holding figure while that read has not landed', () => {
    renderFields({ side: 'sell', heldQuantity: null })
    expect(screen.queryByLabelText('shares held')).not.toBeInTheDocument()
  })

  it('reuses the idempotency key after a failure that may still have placed the order', async () => {
    // A 500 or a lost reply may have committed it, so the retry has to carry
    // the same key for the ledger's own replay to recognise it — a fresh key
    // would rest a second order (ledger-service.md).
    const keys: unknown[] = []
    server.use(http.post(ORDERS, async ({ request }) => {
      const body = await request.json() as Record<string, unknown>
      keys.push(body.idempotency_key)
      return keys.length === 1
        ? HttpResponse.json({ error: { code: 'internal_error', message: 'boom' } }, { status: 500 })
        : HttpResponse.json(orderResponse(), { status: 201 })
    }))
    const { actor } = renderFields()

    await fillIn(actor, '0.3', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))
    await screen.findByRole('alert')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    await waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).toBe(keys[1])
  })

  it.each([
    ['a 500', 500, 'internal_error'],
    ['idempotency_key_reused', 409, 'idempotency_key_reused'],
  ])('re-reads the orders after %s, which may mean the order was placed', async (_name, status, code) => {
    // A 5xx or a lost reply may have committed the order, and a reused key
    // means an earlier request with it did: either way the list below may
    // already hold it.
    server.use(http.post(ORDERS, () => HttpResponse.json({ error: { code, message: 'no' } }, { status })))
    const { actor, onPlaced } = renderFields()

    await fillIn(actor, '0.3', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    await screen.findByRole('alert')
    expect(onPlaced).toHaveBeenCalled()
  })

  it('takes a fresh key after a refusal that definitely placed nothing', async () => {
    const keys: unknown[] = []
    server.use(http.post(ORDERS, async ({ request }) => {
      const body = await request.json() as Record<string, unknown>
      keys.push(body.idempotency_key)
      return keys.length === 1
        ? HttpResponse.json({ error: { code: 'market_not_binary', message: 'no' } }, { status: 409 })
        : HttpResponse.json(orderResponse(), { status: 201 })
    }))
    const { actor } = renderFields()

    await fillIn(actor, '0.3', '50')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))
    await screen.findByRole('alert')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    await waitFor(() => expect(keys).toHaveLength(2))
    expect(keys[0]).not.toBe(keys[1])
  })
})
