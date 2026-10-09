import { useRef, useState } from 'react'
import { describeTradeError, errorCode, isDefiniteRejection } from '../../api/errors'
import { postTrade, type TradeSide } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { useBalance } from '../../context/BalanceContext'
import { useTradeQuote } from '../../hooks/useTradeQuote'
import { formatCreditsPrecise, unsignedCredits } from '../../utils/formatCredits'
import { formatPrice } from '../../utils/formatPrice'
import Button from '../ui/Button'
import { controlClass } from '../ui/controlClass'
import { selectableClass } from '../ui/selectableClass'

// Buy and Sell are the same toggle shape with a different active colour.
function sideToggleClass(active: boolean, activeColor: 'success' | 'danger'): string {
  const activeClass = activeColor === 'success' ? 'border-success bg-success/10 text-success' : 'border-danger bg-danger/10 text-danger'
  return `flex-1 cursor-pointer rounded-control border py-2.5 text-sm font-semibold transition ${
    active ? activeClass : selectableClass(false)
  }`
}

export default function TradingPanel({ marketId, outcomes }: {
  marketId: string
  outcomes: PublicOutcome[]
}) {
  const { refetch: refetchBalance } = useBalance()

  const [side, setSide] = useState<TradeSide>('buy')
  const [outcomeId, setOutcomeId] = useState(outcomes[0]?.id ?? '')
  const [quantity, setQuantity] = useState('')
  const inputKey = `${marketId}:${outcomeId}:${side}:${quantity}`

  const { quantityInputError, preview, previewError, previewLoading, refetchNow } = useTradeQuote({ marketId, outcomeId, side, quantity })

  const [submitting, setSubmitting] = useState(false)
  const [submitErrorFor, setSubmitErrorFor] = useState('')
  const [submitError, setSubmitError] = useState('')
  const [lastTradeFor, setLastTradeFor] = useState('')
  const [lastTrade, setLastTrade] = useState<{ side: TradeSide; quantity: string; outcomeLabel: string; total: string } | null>(null)
  // Regenerated only once the server has given a definite answer: success,
  // or a 4xx that means the trade did not happen. A lost reply or a 5xx
  // means the server may have committed it anyway, and the only safe retry
  // is one that reuses the key so the server's own idempotency check
  // recognises it as the same trade rather than a new one
  // (ledger-service.md's idempotency section) — throwing the key away on
  // every failure, including these, is how a dropped connection buys twice.
  const idempotencyKeyRef = useRef(crypto.randomUUID())

  const visibleSubmitError = submitErrorFor === inputKey ? submitError : ''
  const visibleLastTrade = lastTradeFor === inputKey ? lastTrade : null

  const selectedOutcome = outcomes.find(o => o.id === outcomeId)
  const priceNow = preview?.prices.find(p => p.outcome_id === outcomeId)
  const priceAfter = preview?.post_trade_prices.find(p => p.outcome_id === outcomeId)

  const submit = async () => {
    if (!preview || submitting) return
    setSubmitting(true)
    setSubmitErrorFor(inputKey)
    setSubmitError('')
    try {
      const result = await postTrade(marketId, {
        outcome_id: outcomeId,
        side,
        quantity: quantity.trim(),
        state_version: preview.state_version,
        idempotency_key: idempotencyKeyRef.current,
      })
      idempotencyKeyRef.current = crypto.randomUUID()
      // Keyed to the post-clear input (quantity ""), which is what the
      // confirmation renders against once this commits.
      setLastTradeFor(`${marketId}:${outcomeId}:${side}:`)
      setLastTrade({ side, quantity: result.quantity, outcomeLabel: selectedOutcome?.label ?? '', total: result.total })
      setQuantity('')
      refetchBalance()
    } catch (err) {
      const code = errorCode(err)
      if (code === 'quote_stale') {
        // Same click, same key: get the new price and let the trader
        // confirm again rather than resubmitting the stale version
        // automatically. refetchNow is the same fetch the live preview
        // uses, so if it fails too, previewError already shows why — clear
        // submitError rather than leaving "Getting a fresh quote…" frozen
        // on screen regardless of how the re-quote turns out.
        setSubmitError(describeTradeError(err))
        const freshQuote = await refetchNow()
        setSubmitError(freshQuote ? 'Prices moved. Check the new quote and confirm again.' : '')
      } else {
        setSubmitError(describeTradeError(err))
        if (isDefiniteRejection(err)) idempotencyKeyRef.current = crypto.randomUUID()
      }
    } finally {
      setSubmitting(false)
    }
  }

  const canSubmit = !!preview && !previewLoading && !submitting && !previewError

  return (
    <div>
      <div className="mb-3 flex gap-2">
        <button
          type="button"
          onClick={() => setSide('buy')}
          aria-pressed={side === 'buy'}
          className={sideToggleClass(side === 'buy', 'success')}
        >
          Buy
        </button>
        <button
          type="button"
          onClick={() => setSide('sell')}
          aria-pressed={side === 'sell'}
          className={sideToggleClass(side === 'sell', 'danger')}
        >
          Sell
        </button>
      </div>

      <div className="mb-3 flex gap-2">
        {outcomes.map(outcome => (
          <button
            key={outcome.id}
            type="button"
            onClick={() => setOutcomeId(outcome.id)}
            aria-pressed={outcomeId === outcome.id}
            className={`flex-1 cursor-pointer rounded-control border px-3 py-2.5 text-sm font-medium transition ${selectableClass(outcomeId === outcome.id)}`}
          >
            {outcome.label}
          </button>
        ))}
      </div>

      {visibleLastTrade && (
        <p role="status" className="mb-3 rounded-control bg-success/10 px-4 py-3 text-[13px] text-success">
          {visibleLastTrade.side === 'buy' ? 'Bought' : 'Sold'} {visibleLastTrade.quantity} {visibleLastTrade.outcomeLabel} for {formatCreditsPrecise(unsignedCredits(visibleLastTrade.total))} credits.
        </p>
      )}

      <label htmlFor="trade-quantity" className="mb-1.5 block text-[13px] font-semibold text-smu-navy">
        Quantity (shares)
      </label>
      <input
        id="trade-quantity"
        type="number"
        min="0"
        step="0.0001"
        value={quantity}
        onChange={e => setQuantity(e.target.value)}
        placeholder="e.g. 10"
        className={`${controlClass(!!quantityInputError || !!previewError)} mb-3 h-[46px]`}
      />

      {quantityInputError && <p role="alert" className="mb-3 text-xs text-danger">{quantityInputError}</p>}

      {!quantityInputError && previewLoading && <p className="mb-3 text-xs text-subtle">Getting a quote…</p>}

      {!quantityInputError && previewError && <p role="alert" className="mb-3 text-xs text-danger">{previewError}</p>}

      {preview && !previewLoading && (
        <div aria-label="trade preview" className="mb-3 rounded-control bg-smu-cream px-4 py-3 text-[13px]">
          <div className="flex justify-between">
            <span className="text-muted">{side === 'buy' ? 'Cost' : 'Proceeds'}</span>
            <strong className="text-smu-navy">{formatCreditsPrecise(unsignedCredits(preview.total))} credits</strong>
          </div>
          <div className="mt-1 flex justify-between">
            <span className="text-muted">Average price</span>
            <span className="text-smu-navy">{formatPrice(preview.average_price)}</span>
          </div>
          {priceNow && priceAfter && (
            <div className="mt-1 flex justify-between">
              <span className="text-muted">{selectedOutcome?.label} price</span>
              <span className="text-smu-navy">{formatPrice(priceNow.price)} → {formatPrice(priceAfter.price)}</span>
            </div>
          )}
        </div>
      )}

      {visibleSubmitError && <p role="alert" className="mb-3 text-xs text-danger">{visibleSubmitError}</p>}

      <Button variant="primary" size="block" onClick={submit} disabled={!canSubmit}>
        {submitting
          ? 'Submitting…'
          : `${side === 'buy' ? 'Buy' : 'Sell'} ${selectedOutcome?.label ?? ''}`}
      </Button>
    </div>
  )
}
