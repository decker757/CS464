import { useRef, useState } from 'react'
import { describeOrderError, errorCode, isDefiniteRejection } from '../../api/errors'
import { placeOrder, type Order, type TradeSide } from '../../api/ledgerApi'
import { useBalance } from '../../context/BalanceContext'
import { formatCreditsPrecise, isZeroCredits } from '../../utils/formatCredits'
import { formatPrice } from '../../utils/formatPrice'
import { limitPriceError, quantityError } from '../../utils/orderInput'
import Button from '../ui/Button'
import { controlClass } from '../ui/controlClass'

interface LimitOrderFieldsProps {
  marketId: string
  // Chosen above, in the panel the two order types share, so a trader picks
  // a side and an outcome once rather than twice.
  side: TradeSide
  outcomeId: string
  outcomeLabel: string
  // What the trader holds of this outcome, as the ledger's own decimal
  // string; null while that read has not landed, which shows no figure.
  heldQuantity: string | null
  // An order was placed, so the open-orders list, the balance and the
  // holdings are all stale.
  onPlaced: () => void
}

/** What the placement did, for the line shown after it commits. */
function placedMessage(order: Order, outcomeLabel: string): string {
  const action = order.side === 'buy' ? 'Buy' : 'Sell'
  const at = `${action} ${order.quantity} ${outcomeLabel} at ${formatPrice(order.limit_price)}`
  if (order.status === 'filled') {
    return `${at}: filled in full at ${formatPrice(order.average_fill_price ?? order.limit_price)}.`
  }
  if (!isZeroCredits(order.filled_quantity)) {
    return `${at}: ${order.filled_quantity} filled, the rest is resting.`
  }
  return `${at}: resting until the price reaches it.`
}

/**
 * The limit price and quantity half of the order ticket ([T-6] #26, ADR
 * 0024). A buy fills at its limit or below, a sell at its limit or above;
 * whatever cannot fill now rests until a later trade moves the price onto
 * it, or the trader cancels it.
 *
 * There is no quote and no preview here, unlike a market order: the limit is
 * the protection, so the ledger takes no `state_version`, and a price check
 * would refuse an order for the very move it was placed to catch. What the
 * order reserves comes back on the response rather than being computed here
 * — the ledger rounds the budget up to the tick, and a second
 * implementation of that rounding could only disagree with it.
 */
export default function LimitOrderFields({
  marketId, side, outcomeId, outcomeLabel, heldQuantity, onPlaced,
}: LimitOrderFieldsProps) {
  const { refetch: refetchBalance } = useBalance()

  const [limitPrice, setLimitPrice] = useState('')
  const [quantity, setQuantity] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [placed, setPlaced] = useState('')
  // Regenerated only once the server has given a definite answer, exactly as
  // on the trade route: a lost reply or a 5xx may still have placed the
  // order, and the only safe retry reuses the key so the ledger's own replay
  // recognises it rather than resting a second order (ledger-service.md).
  const idempotencyKeyRef = useRef(crypto.randomUUID())

  const priceError = limitPriceError(limitPrice)
  const sizeError = quantityError(quantity)
  const ready = !!limitPrice.trim() && !!quantity.trim() && !priceError && !sizeError
  const canSubmit = ready && !submitting

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setSubmitError('')
    setPlaced('')
    try {
      const result = await placeOrder(marketId, {
        outcome_id: outcomeId,
        side,
        limit_price: limitPrice.trim(),
        quantity: quantity.trim(),
        idempotency_key: idempotencyKeyRef.current,
      })
      idempotencyKeyRef.current = crypto.randomUUID()
      setPlaced(placedMessage(result.order, outcomeLabel))
      setLimitPrice('')
      setQuantity('')
      // A placement that filled moved credits, and one that rests reserves
      // them — either way the balance route's figures changed.
      refetchBalance()
      onPlaced()
    } catch (err) {
      setSubmitError(describeOrderError(err))
      if (isDefiniteRejection(err)) idempotencyKeyRef.current = crypto.randomUUID()
      // An order refused for crossing the book was blocked by a resting
      // order that may be one of the trader's own, so the list below is
      // worth re-reading.
      if (errorCode(err) === 'order_crosses_book') onPlaced()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      {placed && (
        <p role="status" className="mb-3 rounded-control bg-success/10 px-4 py-3 text-[13px] text-success-strong">
          {placed}
        </p>
      )}

      <label htmlFor="limit-price" className="mb-1.5 block text-[13px] font-semibold text-smu-navy">
        Limit price
      </label>
      <input
        id="limit-price"
        type="number"
        min="0.0001"
        max="0.9999"
        step="0.0001"
        value={limitPrice}
        onChange={e => setLimitPrice(e.target.value)}
        placeholder="e.g. 0.55"
        className={`${controlClass(!!priceError)} mb-1 h-[46px]`}
      />
      {priceError
        ? <p role="alert" className="mb-3 text-xs text-danger">{priceError}</p>
        : (
          <p className="mb-3 text-xs text-subtle">
            {side === 'buy' ? 'Fills at this price or below.' : 'Fills at this price or above.'}
          </p>
        )}

      <div className="mb-1.5 flex items-baseline justify-between">
        <label htmlFor="limit-quantity" className="block text-[13px] font-semibold text-smu-navy">
          Quantity (shares)
        </label>
        {side === 'sell' && heldQuantity !== null && (
          <span aria-label="shares held" className="text-[13px] text-muted">
            You hold {formatCreditsPrecise(heldQuantity)} shares
          </span>
        )}
      </div>
      <input
        id="limit-quantity"
        type="number"
        min="0"
        step="0.0001"
        value={quantity}
        onChange={e => setQuantity(e.target.value)}
        placeholder="e.g. 100"
        className={`${controlClass(!!sizeError)} mb-1 h-[46px]`}
      />
      {sizeError && <p role="alert" className="mb-3 text-xs text-danger">{sizeError}</p>}

      {submitError && <p role="alert" className="mt-2 mb-3 text-xs text-danger">{submitError}</p>}

      <Button variant="primary" size="block" onClick={submit} disabled={!canSubmit} className="mt-2">
        {submitting ? 'Placing…' : `Place ${side} order`}
      </Button>
    </div>
  )
}
