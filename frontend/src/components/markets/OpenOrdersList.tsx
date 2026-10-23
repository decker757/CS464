import { useState } from 'react'
import { describeOrderError, errorCode } from '../../api/errors'
import { cancelOrder, type Order } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { formatCreditsPrecise } from '../../utils/formatCredits'
import { formatPrice } from '../../utils/formatPrice'
import { HeaderCell, NumberCell, TableFrame, TableHeaderRow } from '../ui/Table'
import Button from '../ui/Button'
import SectionCard from '../ui/SectionCard'

interface OpenOrdersListProps {
  orders: Order[]
  // This market's outcomes, for the labels: the ledger carries ids only
  // (ledger-service.md), and every order here is on the market whose page
  // this is.
  outcomes: PublicOutcome[]
  // A cancel was answered, either way, so the list and the balance are both
  // stale.
  onCancelled: () => void
}

/** What the last cancel's refusal said, and whether the trader has to act on it. */
interface CancelNotice {
  text: string
  isError: boolean
}

/**
 * The trader's resting orders on one market, with what each has filled so
 * far and a cancel ([T-6] #26's fourth criterion, [T-8] #28).
 *
 * Mounted whether or not there are orders, and renders nothing when there
 * are none and no cancel has been refused. A refused cancel re-reads the
 * list, and when the order was the market's only one that read comes back
 * empty: the message saying why the cancel did nothing has to outlive the
 * row it was about, so it lives here rather than with the rows.
 *
 * `reserved_credits` or `reserved_shares` is shown as "held", since that is
 * the figure explaining why a balance looks smaller than it did: a resting
 * buy's credits are still in `balance` but not in `available` (ADR 0024).
 */
export default function OpenOrdersList({ orders, outcomes, onCancelled }: OpenOrdersListProps) {
  // The order a cancel is in flight for, so only its own button shows the
  // pending label and a second click cannot fire a second request.
  const [cancelling, setCancelling] = useState('')
  const [notice, setNotice] = useState<CancelNotice | null>(null)

  const cancel = async (orderId: string) => {
    if (cancelling) return
    setCancelling(orderId)
    setNotice(null)
    try {
      await cancelOrder(orderId)
      onCancelled()
    } catch (err) {
      // order_already_cancelled is a retried cancel landing: ledger-service.md
      // says to treat it as done, so it is a status line and not an alert.
      setNotice({ text: describeOrderError(err), isError: errorCode(err) !== 'order_already_cancelled' })
      // Even a refused cancel moves the list on: order_already_filled and
      // order_already_cancelled both mean the row on screen is out of date,
      // and the message above says which it was.
      onCancelled()
    } finally {
      setCancelling('')
    }
  }

  if (orders.length === 0 && !notice) return null

  return (
    <SectionCard title="Your open orders" className="mb-6">
      {notice?.isError && <p role="alert" className="mb-3 text-xs text-danger">{notice.text}</p>}
      {notice && !notice.isError && <p role="status" className="mb-3 text-xs text-muted">{notice.text}</p>}

      {orders.length > 0 && (
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
      )}
    </SectionCard>
  )
}
