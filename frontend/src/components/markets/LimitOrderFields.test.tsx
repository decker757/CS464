import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { BalanceContext } from '../../context/BalanceContext'
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
  heldQuantity?: string | null
  onPlaced?: () => void
}

function renderFields({ side = 'buy', heldQuantity = '100.0000', onPlaced = vi.fn() }: FieldsOptions = {}) {
  render(
    <BalanceContext.Provider value={{ balance: '1000.0000', available: '1000.0000', refetch: async () => {} }}>
      <LimitOrderFields
        marketId={MARKET_ID}
        side={side}
        outcomeId="o-yes"
        outcomeLabel="Yes"
        heldQuantity={heldQuantity}
        onPlaced={onPlaced}
      />
    </BalanceContext.Provider>,
  )
  return { actor: userEvent.setup(), onPlaced }
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

    expect(await screen.findByRole('status')).toHaveTextContent(/resting until the price reaches it/i)
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

    // The average fill price, not the limit: what it actually cost.
    expect(await screen.findByRole('status')).toHaveTextContent(/filled in full at 51\.3%/i)
  })

  it('says how much filled when only part of the order did', async () => {
    server.use(http.post(ORDERS, () => HttpResponse.json(
      orderResponse({ filled_quantity: '20.5465', average_fill_price: '0.5750', reserved_credits: '48.1848' }),
      { status: 201 },
    )))
    const { actor } = renderFields()

    await fillIn(actor, '0.6', '100')
    await actor.click(screen.getByRole('button', { name: /place buy order/i }))

    expect(await screen.findByRole('status')).toHaveTextContent(/20\.5465 filled, the rest is resting/i)
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

    expect(await screen.findByRole('alert')).toHaveTextContent(/must be below 70\.0%/i)
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

  it('shows the holding on a sell, so the trader knows what they can offer', () => {
    renderFields({ side: 'sell', heldQuantity: '42.0000' })
    expect(screen.getByLabelText('shares held')).toHaveTextContent('42.0000')
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
