import { useState } from 'react'
import { describeOrderError } from '../../api/errors'
import { cancelOrder, type Order } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { formatCreditsPrecise } from '../../utils/formatCredits'
import { formatPrice } from '../../utils/formatPrice'
import { HeaderCell, NumberCell, TableFrame, TableHeaderRow } from '../ui/Table'
import Button from '../ui/Button'

interface OpenOrdersListProps {
  orders: Order[]
  // This market's outcomes, for the labels: the ledger carries ids only
  // (ledger-service.md), and every order here is on the market whose page
  // this is.
  outcomes: PublicOutcome[]
  // An order left the book, so the list and the balance are both stale.
  onCancelled: () => void
}

/**
 * The trader's resting orders on one market, with what each has filled so
 * far and a cancel ([T-6] #26's fourth criterion, [T-8] #28).
 *
 * `reserved_credits` or `reserved_shares` is shown as "held", since that is
 * the figure explaining why a balance looks smaller than it did: a resting
 * buy's credits are still in `balance` but not in `available` (ADR 0024).
 */
export default function OpenOrdersList({ orders, outcomes, onCancelled }: OpenOrdersListProps) {
  // The order a cancel is in flight for, so only its own button shows the
  // pending label and a second click cannot fire a second request.
  const [cancelling, setCancelling] = useState('')
  const [cancelError, setCancelError] = useState('')

  const cancel = async (orderId: string) => {
    if (cancelling) return
    setCancelling(orderId)
    setCancelError('')
    try {
      await cancelOrder(orderId)
      onCancelled()
    } catch (err) {
      setCancelError(describeOrderError(err))
      // Even a refused cancel moves the list on: order_already_filled and
      // order_already_cancelled both mean the row on screen is out of date,
      // and the message above says which it was.
      onCancelled()
    } finally {
      setCancelling('')
    }
  }

  return (
    <>
      {cancelError && <p role="alert" className="mb-3 text-xs text-danger">{cancelError}</p>}

      <TableFrame>
        <thead>
          <TableHeaderRow>
            <HeaderCell>Order</HeaderCell>
            <HeaderCell align="right">Limit</HeaderCell>
            <HeaderCell align="right">Quantity</HeaderCell>
            <HeaderCell align="right">Filled</HeaderCell>
            <HeaderCell align="right">Held</HeaderCell>
            <HeaderCell align="right"><span className="sr-only">Actions</span></HeaderCell>
          </TableHeaderRow>
        </thead>
        <tbody>
          {orders.map(order => {
            const label = outcomes.find(o => o.id === order.outcome_id)?.label ?? 'Unknown outcome'
            // A buy holds credits, a sell holds shares; the other field is
            // null, so only one of the two is ever a figure.
            const held = order.side === 'buy'
              ? `${formatCreditsPrecise(order.reserved_credits ?? '0')} credits`
              : `${formatCreditsPrecise(order.reserved_shares ?? '0')} shares`
            return (
              <tr key={order.id} className="border-b border-line last:border-0">
                <td className="px-5 py-3 text-smu-navy">
                  <span className={order.side === 'buy' ? 'font-semibold text-success' : 'font-semibold text-danger'}>
                    {order.side === 'buy' ? 'Buy' : 'Sell'}
                  </span>{' '}
                  {label}
                </td>
                <NumberCell>{formatPrice(order.limit_price)}</NumberCell>
                <NumberCell>{formatCreditsPrecise(order.quantity)}</NumberCell>
                <NumberCell>{formatCreditsPrecise(order.filled_quantity)}</NumberCell>
                <NumberCell>{held}</NumberCell>
                <td className="px-5 py-3 text-right">
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => cancel(order.id)}
                    disabled={!!cancelling}
                    aria-label={`Cancel ${order.side} ${label} at ${formatPrice(order.limit_price)}`}
                  >
                    {cancelling === order.id ? 'Cancelling…' : 'Cancel'}
                  </Button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </TableFrame>
    </>
  )
}
