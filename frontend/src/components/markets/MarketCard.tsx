import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { PriceState } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import { formatTimeAgo } from '../../utils/formatDateTime'
import { formatPrice } from '../../utils/formatPrice'
import Card from '../ui/Card'
import { formatCloseCountdown, tradingStopped } from './marketStatus'
import StatusBadge from './StatusBadge'

interface MarketCardProps {
  market: PublicMarketSummary
  /** The market's price snapshot; undefined while loading or if it failed. */
  snapshot?: PriceState
}

// How often the card re-reads the clock. The countdown's smallest unit is a
// minute, so ten seconds keeps it honest to within a tick without re-rendering
// the grid every second.
const COUNTDOWN_TICK_MS = 10_000

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

export default function MarketCard({ market, snapshot }: MarketCardProps) {
  // A countdown computed once at render freezes, and a frozen one says a market
  // is still trading after it has closed. Trading stops on the clock, not when
  // the sweep writes CLOSED (ADR 0011), so the card keeps its own clock.
  const [now, setNow] = useState(() => new Date())
  // A market that has already stopped reads "Closed" for as long as the tab is
  // open, so there is nothing for a clock to change. The last tick before this
  // turns true is the one that writes "Closed", and it stops the clock itself.
  const hasStopped = tradingStopped(market.status, market.close_time, now)
  // Zero is the book's own statement that it has never been traded, and the
  // only thing that distinguishes that from a market the crowd has priced at
  // the same number (ledgerApi.ts). A market with no snapshot yet is a
  // different state again — see the dash below — so this asks for the value
  // and not merely for a falsy one.
  const hasNeverTraded = snapshot?.state_version === 0
  // Only a traded book has a last trade: on an untraded one `occurred_at` is
  // when the book was opened, which is not a price anybody moved. Read off the
  // clock the card already keeps, so it ages with the countdown beside it
  // rather than freezing at first render ([X-1] #364). It does stop ageing
  // once the market stops trading, with the ticker — but a market that can no
  // longer trade can no longer move its book either, and the whole card is a
  // frozen read at that point (the list's own "Prices updated" line says when).
  const lastTradeAgo = snapshot && !hasNeverTraded ? formatTimeAgo(snapshot.occurred_at, now) : null

  useEffect(() => {
    if (hasStopped) return
    const ticker = setInterval(() => setNow(new Date()), COUNTDOWN_TICK_MS)
    return () => clearInterval(ticker)
  }, [hasStopped])

  return (
    <Link to={`/markets/${market.id}`} className="block">
      <Card interactive className="h-full cursor-pointer px-6 py-5">
        <div className="mb-3 flex items-center justify-between gap-2">
          <StatusBadge status={market.status} />
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
                    what a price still loading, or one that failed, reads as
                    (#431 separates those two). */}
                {!hasNeverTraded && (
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
        {lastTradeAgo && <p className="mt-3 text-xs text-subtle">Last trade {lastTradeAgo}</p>}
      </Card>
    </Link>
  )
}
