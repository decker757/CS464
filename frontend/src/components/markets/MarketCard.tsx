import { Link } from 'react-router-dom'
import type { PriceState } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import { useMarketClock } from '../../hooks/useMarketClock'
import { formatTimeAgo } from '../../utils/formatDateTime'
import { formatPrice } from '../../utils/formatPrice'
import Button from '../ui/Button'
import Card from '../ui/Card'
import { formatCloseCountdown, tradingStopped } from './marketStatus'
import StatusBadge from './StatusBadge'

interface MarketCardProps {
  market: PublicMarketSummary
  /** The market's price snapshot; undefined until it arrives, or if the read failed. */
  snapshot?: PriceState
  /** True once a read of this market's snapshot has failed (usePricesForMarkets). */
  priceReadFailed?: boolean
  /** Reads this market's snapshot again, behind the Retry control. */
  onRetryPrice?: () => void
}

// What a market nobody has traded says instead of a price. An untouched LMSR
// book sits at its seed — a uniform 50.0% on a binary market — so a grid of
// identical 50.0% read as a crowd that was split down the middle on every
// question, when it was nobody at all. The second half is the invitation:
// there is no price to disagree with yet, and the first trade makes one
// ([X-1] #445).
const UNTRADED_INVITATION = 'No trades yet — be the first to set the price.'
// The same fact about a market nobody can trade any more. Worth saying for the
// same reason — the seed price is no more an opinion here than anywhere else —
// but there is no first trade left to invite, and offering one on a market
// that has stopped would be the card's own lie.
const UNTRADED_AND_STOPPED = 'This market closed without a single trade.'

// What a card says while its prices are still on their way, and what it says
// when the read failed. Both were the same dash before [X-1] #431, and the
// hook's own comment said so, which left no way to tell whether to wait or to
// reload — while the detail page said "loading price…" for the first of the
// two. The dash stays in the slot either way, because it is the shape of a
// number that is not there; these say which of the two it is, and the failed
// one comes with a way back.
const PRICES_LOADING = 'Loading prices…'
const PRICES_FAILED = 'Prices could not be loaded.'
// A voided market has no result, so it has no price either: its last prices
// are what the crowd thought of a question that turned out to have no answer
// (ADR 0025). The batch snapshot goes on carrying them, so the card is what
// drops them. Not "refunded what they cost": the refunds can be cut.
const VOIDED_NOTE = 'Voided with no result. Every share still held was refunded.'

export default function MarketCard({ market, snapshot, priceReadFailed = false, onRetryPrice }: MarketCardProps) {
  // The card keeps a clock for its countdown, and stops it once the market
  // has stopped trading (useMarketClock).
  const now = useMarketClock(market)
  const hasStopped = tradingStopped(market.status, market.close_time, now)
  // Every price-shaped thing below asks this first: the prices, the untraded
  // invitation, the last trade, and the loading and failed lines (#526).
  const showsPrices = market.status !== 'voided'
  // Zero is the book's own statement that it has never been traded, and the
  // only thing that distinguishes that from a market the crowd has priced at
  // the same number (ledgerApi.ts). A market with no snapshot yet is a
  // different state again — see the dash below — so this asks for the value
  // and not merely for a falsy one.
  const hasNeverTraded = showsPrices && snapshot?.state_version === 0
  // Only a traded book has a last trade: on an untraded one `occurred_at` is
  // when the book was opened, which is not a price anybody moved. Read off the
  // clock the card already keeps, so it ages with the countdown beside it
  // rather than freezing at first render ([X-1] #364). It does stop ageing
  // once the market stops trading, with the ticker — but a market that can no
  // longer trade can no longer move its book either, and the whole card is a
  // frozen read at that point (the list's own "Prices updated" line says when).
  const lastTradeAgo = showsPrices && snapshot && !hasNeverTraded ? formatTimeAgo(snapshot.occurred_at, now) : null
  // A card with no snapshot is waiting for one or has been refused one, and
  // those are different things to a trader: one is worth waiting out, the
  // other is worth a retry (#431). A failed refresh of a market that already
  // has a price is not either — the card keeps the price it had, and the
  // list's "Prices updated" line is what goes stale.
  const isLoadingPrices = showsPrices && !snapshot && !priceReadFailed
  const hasFailedPrices = showsPrices && !snapshot && priceReadFailed

  return (
    <Card interactive className="h-full">
      {/* The link covers the card's content and stops short of its Retry
          control: a button inside an anchor is not valid HTML, and a click on
          one would open the market as well as read the price again. The
          padding is the link's rather than the card's, so every pixel the
          trader reads as the card still opens it. */}
      <Link to={`/markets/${market.id}`} className="block cursor-pointer px-6 py-5">
        <div className="mb-3 flex items-center justify-between gap-2">
          <StatusBadge status={market.status} audience="trader" />
          <span className="text-right text-xs text-subtle">{formatCloseCountdown(market.status, market.close_time, now)}</span>
        </div>
        <p className="text-[15px] leading-normal font-semibold text-smu-navy">{market.question}</p>
        {/* Wraps onto more rows for a market with many outcomes (up to ten). */}
        <div className="mt-4 flex flex-wrap gap-2">
          {market.outcomes.map(outcome => {
            const outcomePrice = snapshot?.prices.find(price => price.outcome_id === outcome.id)
            return (
              <div key={outcome.id} className="flex min-w-[120px] flex-1 items-center justify-between gap-2 rounded-control bg-smu-cream px-3 py-2">
                <span title={outcome.label} className="truncate text-xs font-semibold tracking-[0.5px] text-muted uppercase">
                  {outcome.label}
                </span>
                {/* An untraded market keeps its outcome names (#214) and shows
                    no number at all. A dash would say the wrong thing: that is
                    what a price still on its way, or one that could not be
                    read, reads as — and the line below the chips says which
                    of those two it is (#431). */}
                {showsPrices && !hasNeverTraded && (
                  <span aria-label={`${outcome.label} price`} className="shrink-0 text-sm font-bold text-smu-navy">
                    {outcomePrice ? formatPrice(outcomePrice.price) : '—'}
                  </span>
                )}
              </div>
            )
          })}
        </div>
        {hasNeverTraded && (
          <p className="mt-3 text-xs text-secondary">{hasStopped ? UNTRADED_AND_STOPPED : UNTRADED_INVITATION}</p>
        )}
        {!showsPrices && <p className="mt-3 text-xs text-secondary">{VOIDED_NOTE}</p>}
        {isLoadingPrices && <p className="mt-3 text-xs text-subtle">{PRICES_LOADING}</p>}
        {lastTradeAgo && <p className="mt-3 text-xs text-subtle">Last trade {lastTradeAgo}</p>}
      </Link>
      {hasFailedPrices && (
        /* No top padding: the link's own bottom padding is the gap. */
        <div className="flex flex-wrap items-center gap-2 px-6 pb-5">
          <p role="alert" className="text-xs text-danger">{PRICES_FAILED}</p>
          <Button variant="outline" size="xs" onClick={onRetryPrice}>Retry</Button>
        </div>
      )}
    </Card>
  )
}
