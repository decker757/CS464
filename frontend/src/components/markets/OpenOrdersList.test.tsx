import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import type { Order } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { server } from '../../test/server'
import OpenOrdersList from './OpenOrdersList'

const LEDGER_BASE = 'http://localhost:8003'
const CANCEL = (id: string) => `${LEDGER_BASE}/ledger/orders/${id}/cancel`

const outcomes: PublicOutcome[] = [
  { id: 'o-yes', position: 0, label: 'Yes' },
  { id: 'o-no', position: 1, label: 'No' },
]

// The live ledger's own shape, from a placement against a running stack.
function order(overrides: Partial<Order> = {}): Order {
  return {
    id: 'ord-1',
    market_id: 'mkt-1',
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
  }
}

function renderList(orders: Order[] = [order()]) {
  const onCancelled = vi.fn()
  render(<OpenOrdersList orders={orders} outcomes={outcomes} onCancelled={onCancelled} />)
  return { actor: userEvent.setup(), onCancelled }
}

describe('OpenOrdersList', () => {
  it('shows the limit price, quantity and filled-so-far of each order', () => {
    // [T-6] #26's fourth criterion names exactly these three.
    renderList([order({ filled_quantity: '20.5465' })])

    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByText('Yes')).toBeInTheDocument()
    // The limit in the units the ticket's field takes (0.30), not 30.0%.
    expect(row).toHaveTextContent('0.3000')
    expect(row).toHaveTextContent('50.0000')
    expect(row).toHaveTextContent('20.5465')
  })

  it('shows a buy holding credits and a sell holding shares', () => {
    // reserved_credits is a buy's and reserved_shares a sell's; the other is
    // null on each, so only one is ever a figure (ledger-service.md).
    renderList([
      order({ reserved_credits: '15.0000', reserved_shares: null }),
      order({ id: 'ord-2', side: 'sell', outcome_id: 'o-no', reserved_credits: null, reserved_shares: '8.0000' }),
    ])

    const [, buyRow, sellRow] = screen.getAllByRole('row')
    expect(buyRow).toHaveTextContent('15.0000 credits')
    expect(sellRow).toHaveTextContent('8.0000 shares')
  })

  it('cancels the order its button belongs to', async () => {
    let cancelled = ''
    server.use(http.post(CANCEL('ord-2'), () => {
      cancelled = 'ord-2'
      return HttpResponse.json({ order: order({ id: 'ord-2', status: 'cancelled' }), released_credits: '15.0000', released_shares: null })
    }))
    const { actor, onCancelled } = renderList([order(), order({ id: 'ord-2', limit_price: '0.4000' })])

    await actor.click(screen.getByRole('button', { name: 'Cancel buy Yes at 0.4000' }))

    await waitFor(() => expect(cancelled).toBe('ord-2'))
    expect(onCancelled).toHaveBeenCalled()
  })

  it('tells the trader when the order filled before the cancel reached it, and moves the list on', async () => {
    // ledger-service.md: a cancel and a fill never both win. The row on
    // screen is stale either way, so the list is re-read.
    server.use(http.post(CANCEL('ord-1'), () => HttpResponse.json(
      { error: { code: 'order_already_filled', message: 'too late' } }, { status: 409 },
    )))
    const { actor, onCancelled } = renderList()

    await actor.click(screen.getByRole('button', { name: /cancel/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/filled before the cancel/i)
    expect(onCancelled).toHaveBeenCalled()
  })

  it('treats an already-cancelled order as done rather than an error to act on', async () => {
    // A retried cancel lands here; the wording says so instead of implying
    // something went wrong.
    server.use(http.post(CANCEL('ord-1'), () => HttpResponse.json(
      { error: { code: 'order_already_cancelled', message: 'done' } }, { status: 409 },
    )))
    const { actor } = renderList()

    await actor.click(screen.getByRole('button', { name: /cancel/i }))

    // ledger-service.md: "treat it as done". So it reads as a status line,
    // not as an alert the trader has to act on.
    expect(await screen.findByRole('status')).toHaveTextContent('That order was already cancelled.')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not send a second cancel while one is in flight', async () => {
    let calls = 0
    server.use(http.post(CANCEL('ord-1'), async () => {
      calls += 1
      await new Promise(resolve => setTimeout(resolve, 50))
      return HttpResponse.json({ order: order({ status: 'cancelled' }), released_credits: '15.0000', released_shares: null })
    }))
    const { actor } = renderList()
    const button = screen.getByRole('button', { name: /cancel/i })

    await actor.click(button)
    await actor.click(button)

    await waitFor(() => expect(calls).toBe(1))
  })
})
