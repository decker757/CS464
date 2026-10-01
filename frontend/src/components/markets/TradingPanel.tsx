import { useEffect, useRef, useState } from 'react'
import { errorCode, tradeErrorDetails } from '../../api/errors'
import { postTrade, previewTrade, type TradePreview, type TradeSide } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { useBalance } from '../../context/BalanceContext'
import { formatCreditsPrecise, unsignedCredits } from '../../utils/formatCredits'
import Button from '../ui/Button'
import { controlClass } from '../ui/controlClass'

const PREVIEW_DEBOUNCE_MS = 300

// What the trader should do for each documented trade/preview error
// (ledger-service.md). Codes with structured details (quote_stale,
// insufficient_funds, insufficient_shares_held) build their message from
// those details instead of a fixed string here.
const FIXED_MESSAGES: Record<string, string> = {
  market_closed: 'This market has closed. Trading is no longer possible.',
  idempotency_key_reused: 'This trade already went through under a different request. Reload the page to see your position.',
  unknown_outcome: 'That outcome is no longer part of this market. Reload the page.',
  quantity_too_large: 'That quantity is too large for this market.',
  cost_below_tick: 'That quantity costs less than the smallest amount this market can charge. Try a larger quantity.',
  proceeds_below_tick: 'That quantity would pay out less than the smallest amount this market can pay. Try a larger quantity.',
  market_not_found: 'This market is not available.',
}

// Buy and Sell are the same toggle shape with a different active colour.
function sideToggleClass(active: boolean, activeColor: 'success' | 'danger'): string {
  const activeClass = activeColor === 'success' ? 'border-success bg-success/10 text-success' : 'border-danger bg-danger/10 text-danger'
  return `flex-1 cursor-pointer rounded-control border py-2.5 text-sm font-semibold transition ${
    active ? activeClass : 'border-smu-navy/20 text-smu-navy hover:border-smu-navy/40'
  }`
}

function describeTradeError(err: unknown): string {
  const code = errorCode(err)
  if (!code) return 'Something went wrong. Please try again.'
  const details = tradeErrorDetails(err)
  if (code === 'quote_stale') return 'Prices moved while you were looking. Getting a fresh quote…'
  if (code === 'insufficient_funds') return `You have ${details.balance} credits, but this trade needs ${details.required}.`
  if (code === 'insufficient_shares_held') return `You hold ${details.held} shares, but this sell asks for ${details.requested}.`
  return FIXED_MESSAGES[code] ?? 'Something went wrong. Please try again.'
}

// What is wrong with the quantity field, if anything — checked client-side
// so a malformed value never reaches the preview or the trade, where it
// would come back as FastAPI's own {"detail": [...]} shape rather than this
// app's {"error": {...}} envelope (ledger-service.md's quantity rule, D-038:
// > 0, at most 4 decimal places, at most 18 digits in all).
function quantityError(raw: string): string | undefined {
  const trimmed = raw.trim()
  if (!trimmed) return undefined
  const match = /^(\d+)(?:\.(\d+))?$/.exec(trimmed)
  if (!match) return 'Enter a number.'
  const [, wholePart, fractionPart = ''] = match
  if (Number(trimmed) <= 0) return 'Must be greater than zero.'
  if (fractionPart.length > 4) return 'At most 4 decimal places.'
  if (wholePart.length + fractionPart.length > 18) return 'That quantity is too large.'
  return undefined
}

export default function TradingPanel({ marketId, outcomes }: {
  marketId: string
  outcomes: PublicOutcome[]
}) {
  const { refetch: refetchBalance } = useBalance()

  const [side, setSide] = useState<TradeSide>('buy')
  const [outcomeId, setOutcomeId] = useState(outcomes[0]?.id ?? '')
  const [quantity, setQuantity] = useState('')

  // What inputs the fields below belong to — so a change to side, outcome or
  // quantity hides a stale quote or confirmation on this render, before the
  // debounced effect even runs, rather than carrying it one extra frame.
  const inputKey = `${marketId}:${outcomeId}:${side}:${quantity}`
  // A live mirror of inputKey, read inside async callbacks that started on
  // an earlier render — inputKey itself is only ever the value from the
  // render that created the closure. Kept current after every render rather
  // than mutated during one, which React refs must not be.
  const inputKeyRef = useRef(inputKey)
  useEffect(() => { inputKeyRef.current = inputKey })
  const [quoteFor, setQuoteFor] = useState('')
  const [preview, setPreview] = useState<TradePreview | null>(null)
  const [previewError, setPreviewError] = useState('')

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [lastTradeFor, setLastTradeFor] = useState('')
  const [lastTrade, setLastTrade] = useState<{ side: TradeSide; quantity: string; outcomeLabel: string; total: string } | null>(null)
  // Regenerated on every submit click, and reused across an automatic retry
  // of that same click (quote_stale) — never across a new click, since a new
  // click is a different trade even when the fields read the same.
  const idempotencyKeyRef = useRef(crypto.randomUUID())

  const trimmedQuantity = quantity.trim()
  const quantityInputError = quantityError(trimmedQuantity)
  const hasValidQuantity = trimmedQuantity !== '' && !quantityInputError

  const visiblePreview = quoteFor === inputKey ? preview : null
  const visiblePreviewError = quoteFor === inputKey ? previewError : ''
  const visibleLastTrade = lastTradeFor === inputKey ? lastTrade : null
  // True from the moment the inputs change until a quote for them lands —
  // covers both the debounce window and the request itself, with nothing to
  // reset: it is false exactly when quoteFor already matches inputKey.
  const previewLoading = hasValidQuantity && quoteFor !== inputKey

  // Debounced: the preview fires on every keystroke by design
  // (ledger-service.md), and debouncing it is explicitly the frontend's job.
  useEffect(() => {
    if (!hasValidQuantity) return

    let cancelled = false
    const timer = setTimeout(() => {
      previewTrade(marketId, { outcomeId, side, quantity: trimmedQuantity })
        .then(result => {
          if (cancelled) return
          setQuoteFor(inputKey)
          setPreview(result)
          setPreviewError('')
        })
        .catch(err => {
          if (cancelled) return
          setQuoteFor(inputKey)
          setPreview(null)
          setPreviewError(describeTradeError(err))
        })
    }, PREVIEW_DEBOUNCE_MS)

    return () => { cancelled = true; clearTimeout(timer) }
  }, [marketId, outcomeId, side, trimmedQuantity, hasValidQuantity, inputKey])

  const selectedOutcome = outcomes.find(o => o.id === outcomeId)

  const submit = async () => {
    if (!visiblePreview || submitting) return
    setSubmitting(true)
    setSubmitError('')
    try {
      const result = await postTrade(marketId, {
        outcome_id: outcomeId,
        side,
        quantity: trimmedQuantity,
        state_version: visiblePreview.state_version,
        idempotency_key: idempotencyKeyRef.current,
      })
      // A fresh key for the next trade — this one is spent, successfully or not.
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
        // Same click, same key: re-quote and let the trader confirm again
        // rather than resubmitting the stale version automatically. Guarded
        // by inputKey in case the trader changes a field while this is in
        // flight — that debounced effect owns the quote for the new inputs.
        setSubmitError(describeTradeError(err))
        const forKey = inputKey
        previewTrade(marketId, { outcomeId, side, quantity: trimmedQuantity })
          .then(result => {
            if (forKey !== inputKeyRef.current) return
            setQuoteFor(forKey)
            setPreview(result)
            setPreviewError('')
          })
          .catch(() => {})
      } else {
        setSubmitError(describeTradeError(err))
        idempotencyKeyRef.current = crypto.randomUUID()
      }
    } finally {
      setSubmitting(false)
    }
  }

  const canSubmit = !!visiblePreview && !previewLoading && !submitting && !visiblePreviewError

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
            className={`flex-1 cursor-pointer rounded-control border px-3 py-2.5 text-sm font-medium transition ${
              outcomeId === outcome.id ? 'border-smu-navy bg-smu-navy text-white' : 'border-smu-navy/20 text-smu-navy hover:border-smu-navy/40'
            }`}
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
        className={`${controlClass(!!quantityInputError || !!visiblePreviewError)} mb-3 h-[46px]`}
      />

      {quantityInputError && <p role="alert" className="mb-3 text-xs text-danger">{quantityInputError}</p>}

      {!quantityInputError && previewLoading && <p className="mb-3 text-xs text-subtle">Getting a quote…</p>}

      {!quantityInputError && visiblePreviewError && <p role="alert" className="mb-3 text-xs text-danger">{visiblePreviewError}</p>}

      {visiblePreview && !previewLoading && (
        <div aria-label="trade preview" className="mb-3 rounded-control bg-smu-cream px-4 py-3 text-[13px]">
          <div className="flex justify-between">
            <span className="text-muted">{side === 'buy' ? 'Cost' : 'Proceeds'}</span>
            <strong className="text-smu-navy">{formatCreditsPrecise(unsignedCredits(visiblePreview.total))} credits</strong>
          </div>
          <div className="mt-1 flex justify-between">
            <span className="text-muted">Average price</span>
            <span className="text-smu-navy">{visiblePreview.average_price}</span>
          </div>
        </div>
      )}

      {submitError && <p role="alert" className="mb-3 text-xs text-danger">{submitError}</p>}

      <Button variant="primary" size="block" onClick={submit} disabled={!canSubmit}>
        {submitting
          ? 'Submitting…'
          : `${side === 'buy' ? 'Buy' : 'Sell'} ${selectedOutcome?.label ?? ''}`}
      </Button>
    </div>
  )
}
