import { useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { describeInsufficientFunds, describeInsufficientShares, describeTradeError, errorCode, isDefiniteRejection } from '../../api/errors'
import { postTrade, type TradePreview, type TradeSide } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { useBalance } from '../../context/BalanceContext'
import { useTradeKeys, type TradeCheck } from '../../hooks/useTradeKeys'
import { useTradeQuote } from '../../hooks/useTradeQuote'
import { compareCredits, formatCreditsPrecise, isZeroCredits, subtractCredits, unsignedCredits } from '../../utils/formatCredits'
import { priceLimitFor } from '../../utils/priceLimit'
import { isSameTrade, type SentTrade } from '../../utils/sentTrade'
import { describeSharesToSell } from '../../utils/orderInput'
import Button from '../ui/Button'
import { controlClass } from '../ui/controlClass'
import { selectableClass } from '../ui/selectableClass'
import HoldsNoneToSell from './HoldsNoneToSell'
import LimitOrderFields from './LimitOrderFields'
import TradePreviewSummary from './TradePreviewSummary'

type OrderType = 'market' | 'limit'

interface TradingPanelProps {
  marketId: string
  // The logged-in trader. A trade with no answer is kept per trader, so its
  // key outlives a reload without reaching whoever logs in next (#585).
  traderId: string
  outcomes: PublicOutcome[]
  // The outcome to trade. It is chosen on the page's price blocks, where its
  // price is shown, so the panel offers no choice of its own (#429).
  outcomeId: string
  // Whether this market takes limit orders at all: they are binary-markets
  // only, and anything else is refused market_not_binary, so the choice is
  // hidden rather than offered and refused (ledger-service.md, ADR 0024).
  limitOrdersAllowed: boolean
  // A limit order was placed, so the open-orders list, the balance and the
  // holdings are stale.
  onOrderPlaced: () => void
  // What the trader holds of each outcome of this market, as the ledger's own
  // decimal strings; an outcome that is not a key is a holding of zero, since
  // the portfolio route omits a position sold to nothing.
  //
  // Null while that read has not landed, or once it has failed. Then nothing
  // is clamped: the ledger refuses an oversell with the exact figure anyway,
  // where guessing zero here would stop a trader selling shares they own
  // because one request went missing.
  heldByOutcome: Record<string, string> | null
  // What the trader's own live sell orders on this market hold back of each
  // outcome, as the portfolio reports it per position (#410); an outcome that
  // is not a key has nothing held back. The ledger will not sell those shares
  // a second time, so the check below and the Max button both stop at the
  // rest.
  reservedSharesByOutcome: Record<string, string>
  // A trade committed, so the holding above is out of date — the page re-reads
  // it. The balance comes back through BalanceContext's own refetch.
  onTraded: () => void
}

// Buy and Sell are the same toggle shape with a different active colour.
//
// Green for buy and red for sell is the convention Polymarket and Kalshi both
// use, and here it is also the colour the rest of the panel speaks: the
// confirmation banner is `success`, a refusal is `danger`, a profit figure is
// whichever its sign makes it. So this row keeps its tint rather than taking
// the navy fill the order type row above it uses. What #430 found is real all
// the same — two same-shaped rows a few pixels apart disagreeing about what
// "on" looks like — and `ToggleRow` answers it by giving each row its own
// heading, so they read as two questions and not as one control in two
// halves.
function sideToggleClass(active: boolean, activeColor: 'success' | 'danger'): string {
  // Text reads the `-strong` tone, not the plain one: the plain tone on its
  // own 10% tint is 4.14–4.39:1, short of AA's 4.5:1 for text this size
  // (#380). The border keeps the plain tone — it is a shape, not text.
  const activeClass = activeColor === 'success' ? 'border-success bg-success/10 text-success-strong' : 'border-danger bg-danger/10 text-danger-strong'
  return `flex-1 cursor-pointer rounded-control border py-2.5 text-sm font-semibold transition ${
    active ? activeClass : selectableClass(false)
  }`
}

// One labelled row of toggles, headed the way the quantity field below is
// headed. The heading is what separates the two rows; `role="group"` with the
// heading as its name is the same separation for a screen reader, which
// otherwise meets four pressable buttons in a row with nothing to say which
// question each answers.
function ToggleRow({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <p id={id} className="mb-1.5 text-[13px] font-semibold text-smu-navy">
        {label}
      </p>
      <div role="group" aria-labelledby={id} className="flex gap-2">
        {children}
      </div>
    </div>
  )
}

interface TradeToCheck {
  side: TradeSide
  preview: TradePreview | null
  // Trimmed, and '' when the field has already rejected it or is empty.
  quantity: string
  // What a trade may actually spend: the balance less what resting buy
  // orders reserve (ADR 0024). Reading `balance` here would let the screen
  // wave through a trade the ledger then refuses for funds it has already
  // committed to an order.
  available: string | null | undefined
  // The whole position, and the part of it no open sell order holds back.
  heldQuantity: string | null
  sellableQuantity: string | null
  reservedShares: string | undefined
}

/** What the trader's own live sell orders hold back of a holding, never more
 *  than the holding itself. Both come off one portfolio row (#410), so the
 *  clamp matters only if the ledger ever contradicts itself — and then it
 *  keeps a negative out of the line above the field and the refusal under
 *  it. */
function sharesReservedOf(held: string, reserved: string | undefined): string | undefined {
  if (reserved === undefined) return undefined
  return compareCredits(reserved, held) > 0 ? held : reserved
}

/** The part of a holding a new sell may take: the position less what is
 *  reserved, as `sharesReservedOf` caps it. */
function sharesLeftToSell(held: string, reserved: string | undefined): string {
  if (reserved === undefined) return held
  return subtractCredits(held, reserved) ?? held
}

/** What a trader is told about a trade that got no answer or a 5xx: it may
 *  have gone through, and which trade it was, since they may be looking at
 *  another outcome by now (#580). Its key is kept in the tab, so the same
 *  trade sent again is never made twice, a reload included (#585). */
function describeUnconfirmedTrade(trade: SentTrade): string {
  return `Your ${trade.side} of ${trade.quantity} ${trade.outcomeLabel} could not be confirmed, and may have gone through. Check your portfolio before trading again, or send the same trade again: it will not be made twice.`
}

function tradeCheckColor(check: TradeCheck): string {
  if (check.answer === 'unknown') return 'text-danger'
  if (check.answer === 'went-through') return 'text-success-strong'
  return 'text-muted'
}

/** What the ledger said about a trade left unconfirmed before this page
 *  loaded, naming it (#585). */
function describeTradeCheck(check: TradeCheck): string {
  const { side, quantity, outcomeLabel } = check.trade
  if (check.answer === 'went-through') {
    return `Your ${side} of ${quantity} ${outcomeLabel} went through, for ${formatCreditsPrecise(unsignedCredits(check.total))} credits.`
  }
  if (check.answer === 'did-not-go-through') return `Your ${side} of ${quantity} ${outcomeLabel} did not go through.`
  if (check.answer === 'checking') return `Checking whether your ${side} of ${quantity} ${outcomeLabel} went through…`
  return describeUnconfirmedTrade(check.trade)
}

/** The request field a price limit goes in: `max_cost` on a buy and
 *  `min_proceeds` on a sell (#644), or nothing when there is no limit. */
function limitFields(side: TradeSide, priceLimit: string | null): { max_cost?: string; min_proceeds?: string } {
  if (priceLimit === null) return {}
  return side === 'buy' ? { max_cost: priceLimit } : { min_proceeds: priceLimit }
}

/** What a trader is told about a trade refused because its price moved, when
 *  the in-place "Prices moved" line is not on screen to say it: it was not
 *  made, and which trade it was (#589). Nothing about what to do next — the
 *  re-quote may have failed, and the preview's own error says why. */
function describeRefusedTrade(trade: SentTrade): string {
  return `Your ${trade.side} of ${trade.quantity} ${trade.outcomeLabel} was not made: the price moved before it went through.`
}

/** A trade the ledger answered with a 201. `sentMoreThanOnce` when it went
 *  out again under the key of an attempt that got no answer: the ledger makes
 *  one trade per key, so the trader is told one was made, not that this
 *  attempt added another. Which attempt committed cannot be told from here:
 *  a replay's `state_version` matches a new trade's whenever the retry sends
 *  the first attempt's quote. */
interface CompletedTrade {
  side: TradeSide
  quantity: string
  outcomeLabel: string
  total: string
  sentMoreThanOnce: boolean
}

function describeCompletedTrade(trade: CompletedTrade): string {
  const credits = formatCreditsPrecise(unsignedCredits(trade.total))
  if (trade.sentMoreThanOnce) {
    return `Your ${trade.side} of ${trade.quantity} ${trade.outcomeLabel} went through once, for ${credits} credits. It was sent more than once, but only one trade was made.`
  }
  const verb = trade.side === 'buy' ? 'Bought' : 'Sold'
  return `${verb} ${trade.quantity} ${trade.outcomeLabel} for ${credits} credits.`
}

/**
 * Why this trade cannot be sent, as a sentence, or '' when nothing here can
 * tell. The server stays the authority on both figures — this only refuses
 * what the screen can already prove the ledger would refuse, so the trader
 * reads it before the round trip instead of after a failure state (#357).
 *
 * The cost is the one the preview quoted, never a recomputed one, and both
 * comparisons go through compareCredits: these are decimal strings, and
 * parsing them to compare would be the arithmetic formatCredits.ts exists to
 * avoid.
 */
function refusalBeforeSending({
  side, preview, quantity, available, heldQuantity, sellableQuantity, reservedShares,
}: TradeToCheck): string {
  if (side === 'sell') {
    // On the quantity alone, so it answers while the quote is still in
    // flight. A sell pays credits in, so the balance has nothing to say.
    if (heldQuantity === null || sellableQuantity === null || !quantity) return ''
    if (compareCredits(quantity, sellableQuantity) <= 0) return ''
    return describeInsufficientShares(heldQuantity, quantity, reservedShares)
  }

  if (!preview || typeof available !== 'string') return ''
  const cost = unsignedCredits(preview.total)
  if (compareCredits(cost, available) <= 0) return ''
  return describeInsufficientFunds(available, cost)
}

export default function TradingPanel({
  marketId, traderId, outcomes, outcomeId, limitOrdersAllowed, heldByOutcome, reservedSharesByOutcome, onTraded, onOrderPlaced,
}: TradingPanelProps) {
  const { available, refetch: refetchBalance } = useBalance()

  const [orderType, setOrderType] = useState<OrderType>('market')
  const [side, setSide] = useState<TradeSide>('buy')
  const [quantity, setQuantity] = useState('')
  const inputKey = `${marketId}:${outcomeId}:${side}:${quantity}`

  const { quantityInputError, preview, previewError, previewLoading, refetchNow } = useTradeQuote({ marketId, outcomeId, side, quantity })

  const [submitting, setSubmitting] = useState(false)
  const [submitErrorFor, setSubmitErrorFor] = useState('')
  const [submitError, setSubmitError] = useState('')
  const [lastTrade, setLastTrade] = useState<CompletedTrade | null>(null)
  const { keyFor, forgetKey, markSent, markMayHaveCommitted, markRefused, checks, dismissAnsweredChecks } = useTradeKeys({
    marketId,
    traderId,
    onFoundCommitted: () => {
      void refetchBalance()
      onTraded()
    },
  })
  // The trade that got no answer or a 5xx, said on screen whatever the panel
  // now shows, until the next trade is sent (#580).
  const [unconfirmedTrade, setUnconfirmedTrade] = useState<SentTrade | null>(null)
  // The trade refused because its price moved, until the next trade is sent (#589).
  const [refusedTrade, setRefusedTrade] = useState<SentTrade | null>(null)
  const quantityInputRef = useRef<HTMLInputElement>(null)

  const visibleSubmitError = submitErrorFor === inputKey ? submitError : ''
  // A refused trade the market ticket still shows says so in place, under the
  // button. Otherwise — the trader picked another outcome or side, or the
  // Limit ticket, while it was in flight, or its re-quote failed — it says so
  // above the tickets instead, naming it. inputKey leaves out the order type,
  // so the ticket is checked here.
  const isRefusalShownInPlace = orderType === 'market' && visibleSubmitError !== ''
  // A check that has since answered for the same trade says what happened,
  // and its key is gone, so the alert's "will not be made twice" no longer holds.
  const isAnsweredByCheck = (trade: SentTrade) =>
    checks.some(check => (check.answer === 'went-through' || check.answer === 'did-not-go-through') && isSameTrade(check.trade, trade))
  let pastTradeAlert = ''
  if (unconfirmedTrade && !isAnsweredByCheck(unconfirmedTrade)) pastTradeAlert = describeUnconfirmedTrade(unconfirmedTrade)
  else if (refusedTrade && !isRefusalShownInPlace) pastTradeAlert = describeRefusedTrade(refusedTrade)
  // Shown until the trader types the next quantity, and only that: tying it to
  // the outcome too hid a buy's confirmation from a trader who picked another
  // outcome while it was in flight (#566). It names its own outcome, so it
  // cannot be read as being about the one now selected.
  const visibleLastTrade = quantity === '' ? lastTrade : null

  const selectedOutcome = outcomes.find(o => o.id === outcomeId)
  // What the trade will accept if the price moves before the ledger prices it
  // (#644): shown on the preview, and sent with the trade.
  const priceLimit = preview ? priceLimitFor(side, preview.total) : null
  const heldQuantity = heldByOutcome === null ? null : (heldByOutcome[outcomeId] ?? '0')
  // Asked of the selected outcome and of a holding that has landed, so the
  // Sell toggle itself never changes: it stays pressable through a load and
  // through every change of outcome, and only the space below it says why there
  // is nothing to sell (#451). Null is unknown, not none, so it keeps the
  // quantity field. So does a holding that open sell orders hold back in
  // full: the trader owns those shares, and the line over the field says
  // how many of them are free.
  const holdsNoneToSell = side === 'sell' && heldQuantity !== null && isZeroCredits(heldQuantity)
  const reservedShares = heldQuantity === null ? undefined : sharesReservedOf(heldQuantity, reservedSharesByOutcome[outcomeId])
  const sellableQuantity = heldQuantity === null ? null : sharesLeftToSell(heldQuantity, reservedShares)
  // A quantity the field itself rejects says so under the field already; the
  // clamp has nothing to add about it.
  const refusal = refusalBeforeSending({
    side,
    preview,
    quantity: quantityInputError ? '' : quantity.trim(),
    available,
    heldQuantity,
    sellableQuantity,
    reservedShares,
  })

  const submit = async () => {
    if (!preview || submitting) return
    const trade: SentTrade = { outcomeId, outcomeLabel: selectedOutcome?.label ?? '', side, quantity: quantity.trim() }
    const keyEntry = keyFor(trade)
    const idempotencyKey = keyEntry.key
    setSubmitting(true)
    setSubmitErrorFor(inputKey)
    setSubmitError('')
    setUnconfirmedTrade(null)
    setRefusedTrade(null)
    dismissAnsweredChecks()
    markSent(keyEntry)
    try {
      const result = await postTrade(marketId, {
        outcome_id: outcomeId,
        side,
        quantity: trade.quantity,
        state_version: preview.state_version,
        idempotency_key: idempotencyKey,
        ...limitFields(side, priceLimit),
      })
      forgetKey(idempotencyKey)
      setLastTrade({
        side,
        quantity: result.quantity,
        outcomeLabel: trade.outcomeLabel,
        total: result.total,
        sentMoreThanOnce: keyEntry.mayHaveCommitted,
      })
      setQuantity('')
      // `void`: BalanceContext's refetch catches its own failure, and the
      // trade is already committed — the confirmation above does not wait on
      // the new balance arriving (#551).
      void refetchBalance()
      onTraded()
    } catch (err) {
      const code = errorCode(err)
      // A price that moved past the limit is the same "no" as a stale quote:
      // nothing was made, and the trader confirms again at the new price (#644).
      if (code === 'quote_stale' || code === 'price_beyond_limit') {
        // Same click, same key: get the new price and let the trader
        // confirm again rather than resubmitting the stale version
        // automatically. refetchNow is the same fetch the live preview
        // uses, so if it fails too, previewError already shows why — clear
        // submitError rather than leaving "Getting a fresh quote…" frozen
        // on screen regardless of how the re-quote turns out. The alert
        // above the tickets then says the trade was not made (#589).
        markRefused(keyEntry)
        setRefusedTrade(trade)
        setSubmitError(describeTradeError(err))
        const freshQuote = await refetchNow()
        setSubmitError(freshQuote ? 'Prices moved. Check the new quote and confirm again.' : '')
      } else if (isDefiniteRejection(err)) {
        setSubmitError(describeTradeError(err))
        forgetKey(idempotencyKey)
      } else {
        markMayHaveCommitted(keyEntry)
        setUnconfirmedTrade(trade)
      }
    } finally {
      setSubmitting(false)
    }
  }

  // Keeps the outcome: the offer is to buy the one the trader was looking at.
  // The button that was clicked goes with the notice, so focus moves on to
  // the quantity field rather than dropping to the top of the page; flushSync
  // renders that field first, so there is something to focus. On the limit
  // ticket there is no quantity field here, and LimitOrderFields focuses its
  // own first field once this returns.
  const handleBuyInstead = () => {
    flushSync(() => setSide('buy'))
    quantityInputRef.current?.focus()
  }

  const canSubmit = !!preview && !previewLoading && !submitting && !previewError && !refusal

  return (
    <div>
      {/* Which outcome this trades, in words: the choice itself is the price
          blocks above the panel (#429), and the limit ticket's button never
          names it. */}
      <p className="mb-4 text-[13px] break-words text-muted">
        Trading <strong className="font-semibold text-smu-navy">{selectedOutcome?.label}</strong>. Pick another outcome by its price above.
      </p>

      {/* Market or limit first, since it decides which fields follow. Each
          toggle row carries its own heading (#430's ToggleRow), so a screen
          reader meets two labelled questions rather than four loose
          buttons. */}
      {limitOrdersAllowed && (
        <ToggleRow id="order-type-label" label="Order type">
          {(['market', 'limit'] as const).map(type => (
            <button
              key={type}
              type="button"
              aria-pressed={orderType === type}
              onClick={() => setOrderType(type)}
              className={`flex-1 cursor-pointer rounded-control border py-2.5 text-sm font-semibold transition ${selectableClass(orderType === type)}`}
            >
              {type === 'market' ? 'Market' : 'Limit'}
            </button>
          ))}
        </ToggleRow>
      )}

      <ToggleRow id="trade-side-label" label="Buy or sell">
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
      </ToggleRow>

      {/* Above both tickets and the "holds none to sell" offer: it is about a
          trade already sent, whatever the panel shows now (#580, #589). */}
      {pastTradeAlert && <p role="alert" className="mb-3 text-xs text-danger">{pastTradeAlert}</p>}
      {checks.map(check => (
        <p
          key={check.key}
          role={check.answer === 'unknown' ? 'alert' : 'status'}
          className={`mb-3 text-xs ${tradeCheckColor(check)}`}
        >
          {describeTradeCheck(check)}
        </p>
      ))}

      {orderType === 'limit' ? (
        <LimitOrderFields
          marketId={marketId}
          side={side}
          outcomeId={outcomeId}
          outcomeLabel={selectedOutcome?.label ?? ''}
          heldQuantity={heldQuantity}
          sellableQuantity={sellableQuantity}
          holdsNoneToSell={holdsNoneToSell}
          onBuyInstead={handleBuyInstead}
          onPlaced={onOrderPlaced}
        />
      ) : (
      <>
      {visibleLastTrade && (
        <p role="status" className="mb-3 rounded-control bg-success/10 px-4 py-3 text-[13px] break-words text-success-strong">
          {describeCompletedTrade(visibleLastTrade)}
        </p>
      )}

      {/* Always on the market ticket: its live region is empty until there is
          something to say. Shared with the limit ticket (#496). */}
      <HoldsNoneToSell
        holdsNone={holdsNoneToSell}
        outcomeLabel={selectedOutcome?.label ?? ''}
        onBuyInstead={handleBuyInstead}
      />

      {holdsNoneToSell ? null : (
        <>
          <div className="mb-1.5 flex items-baseline justify-between">
            <label htmlFor="trade-quantity" className="block text-[13px] font-semibold text-smu-navy">
              Quantity (shares)
            </label>
            {side === 'sell' && heldQuantity !== null && sellableQuantity !== null && (
              <span aria-label="shares held" className="text-[13px] text-muted">
                {describeSharesToSell(heldQuantity, sellableQuantity)}
                {!isZeroCredits(sellableQuantity) && (
                  <button
                    type="button"
                    onClick={() => setQuantity(sellableQuantity)}
                    className="ml-2 cursor-pointer font-semibold text-info hover:underline"
                  >
                    Max
                  </button>
                )}
              </span>
            )}
          </div>
          <input
            ref={quantityInputRef}
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
            <TradePreviewSummary preview={preview} side={side} outcomeId={outcomeId} outcomeLabel={selectedOutcome?.label ?? ''} priceLimit={priceLimit} />
          )}

          {refusal && <p role="alert" className="mb-3 text-xs text-danger">{refusal}</p>}

          {visibleSubmitError && <p role="alert" className="mb-3 text-xs text-danger">{visibleSubmitError}</p>}

          <Button variant="primary" size="block" onClick={submit} disabled={!canSubmit}>
            {/* Cut short rather than wrapped: the button is one line high, a
                label can be 120 unbroken characters, and the line at the top
                of the panel names the outcome in full (#429). */}
            <span className="min-w-0 truncate px-4">
              {submitting
                ? 'Submitting…'
                : `${side === 'buy' ? 'Buy' : 'Sell'} ${selectedOutcome?.label ?? ''}`}
            </span>
          </Button>
        </>
      )}
      </>
      )}
    </div>
  )
}
