import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { OutcomePrice } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import { formatPrice } from '../../utils/formatPrice'
import Card from '../ui/Card'
import { formatCloseCountdown } from './marketStatus'
import StatusBadge from './StatusBadge'

interface MarketCardProps {
  market: PublicMarketSummary
  /** The market's snapshot prices; undefined while loading or if they failed. */
  prices?: OutcomePrice[]
}

// How often the card re-reads the clock. The countdown's smallest unit is a
// minute, so ten seconds keeps it honest to within a tick without re-rendering
// the grid every second.
const COUNTDOWN_TICK_MS = 10_000

export default function MarketCard({ market, prices }: MarketCardProps) {
  // A countdown computed once at render freezes, and a frozen one says a market
  // is still trading after it has closed. Trading stops on the clock, not when
  // the sweep writes CLOSED (ADR 0011), so the card keeps its own clock.
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const ticker = setInterval(() => setNow(new Date()), COUNTDOWN_TICK_MS)
    return () => clearInterval(ticker)
  }, [])

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
            const outcomePrice = prices?.find(price => price.outcome_id === outcome.id)
            return (
              <div key={outcome.id} className="flex min-w-[120px] flex-1 items-center justify-between gap-2 rounded-control bg-smu-cream px-3 py-2">
                <span title={outcome.label} className="truncate text-xs font-semibold tracking-[0.5px] text-muted uppercase">
                  {outcome.label}
                </span>
                <span aria-label={`${outcome.label} price`} className="shrink-0 text-sm font-bold text-smu-navy">
                  {outcomePrice ? formatPrice(outcomePrice.price) : '—'}
                </span>
              </div>
            )
          })}
        </div>
      </Card>
    </Link>
  )
}
