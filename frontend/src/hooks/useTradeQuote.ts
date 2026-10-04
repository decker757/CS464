import { useEffect, useRef, useState } from 'react'
import { describeTradeError } from '../api/errors'
import { previewTrade, type TradePreview, type TradeSide } from '../api/ledgerApi'

const PREVIEW_DEBOUNCE_MS = 300

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

export interface TradeQuoteInputs {
  marketId: string
  outcomeId: string
  side: TradeSide
  quantity: string
}

interface TradeQuoteState {
  quantityInputError: string | undefined
  // Non-null only once a quote for the exact current inputs has landed —
  // never a quote fetched for a side, outcome or quantity the trader has
  // since moved away from.
  preview: TradePreview | null
  previewError: string
  previewLoading: boolean
  // Fetches a fresh quote for the current inputs right now, bypassing the
  // debounce — for a 409 quote_stale, where the trader needs the new price
  // immediately rather than after another 300ms of nothing happening.
  refetchNow: () => Promise<TradePreview | null>
}

/**
 * The live cost preview for one set of trade inputs, debounced — the route
 * fires on every keystroke by design (ledger-service.md), and debouncing it
 * is explicitly the frontend's job. Every value is tied to the exact inputs
 * it was fetched for, so changing side, outcome or quantity hides a stale
 * quote on the next render rather than one frame late, and a slow
 * out-of-order response for inputs the trader already left behind is
 * dropped in favour of a newer one already on screen.
 */
export function useTradeQuote({ marketId, outcomeId, side, quantity }: TradeQuoteInputs): TradeQuoteState {
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

  const trimmedQuantity = quantity.trim()
  const quantityInputError = quantityError(trimmedQuantity)
  const hasValidQuantity = trimmedQuantity !== '' && !quantityInputError

  const fetchQuote = (forKey: string) => {
    return previewTrade(marketId, { outcomeId, side, quantity: trimmedQuantity })
      .then(result => {
        if (forKey !== inputKeyRef.current) return null
        setQuoteFor(forKey)
        setPreview(result)
        setPreviewError('')
        return result
      })
      .catch((err: unknown) => {
        if (forKey !== inputKeyRef.current) return null
        setQuoteFor(forKey)
        setPreview(null)
        setPreviewError(describeTradeError(err))
        return null
      })
  }

  useEffect(() => {
    if (!hasValidQuantity) return
    const timer = setTimeout(() => fetchQuote(inputKey), PREVIEW_DEBOUNCE_MS)
    return () => clearTimeout(timer)
    // fetchQuote is recreated every render from the same inputs already
    // listed below; it is not a stable identity worth tracking separately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marketId, outcomeId, side, trimmedQuantity, hasValidQuantity, inputKey])

  return {
    quantityInputError,
    preview: quoteFor === inputKey ? preview : null,
    previewError: quoteFor === inputKey ? previewError : '',
    // Covers both the debounce window and the request itself, with nothing
    // to reset: it is false exactly when quoteFor already matches inputKey.
    previewLoading: hasValidQuantity && quoteFor !== inputKey,
    refetchNow: () => fetchQuote(inputKey),
  }
}
