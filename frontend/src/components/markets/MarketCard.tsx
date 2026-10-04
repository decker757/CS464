import { Link } from 'react-router-dom'
import type { OutcomePrice } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import { formatPrice } from '../../utils/formatPrice'
import Card from '../ui/Card'
import { formatCloseTime } from './marketStatus'
import StatusBadge from './StatusBadge'

interface MarketCardProps {
  market: PublicMarketSummary
  /** The market's snapshot prices; undefined while loading or if they failed. */
  prices?: OutcomePrice[]
}

// GET /public/markets carries no outcome labels, so a card names the outcomes
// at positions 0 and 1 the way the create form fills them in by default.
const OUTCOME_LABELS = ['Yes', 'No']

export default function MarketCard({ market, prices }: MarketCardProps) {
  return (
    <Link to={`/markets/${market.id}`} className="block">
      <Card interactive className="h-full cursor-pointer px-6 py-5">
        <div className="mb-3 flex items-center justify-between gap-2">
          <StatusBadge status={market.status} />
          <span className="text-right text-xs text-subtle">{formatCloseTime(market.status, market.close_time)}</span>
        </div>
        <p className="text-[15px] leading-normal font-semibold text-smu-navy">{market.question}</p>
        <div className="mt-4 flex gap-2">
          {OUTCOME_LABELS.map((label, position) => {
            const outcomePrice = prices?.find(price => price.position === position)
            return (
              <div key={label} className="flex flex-1 items-center justify-between rounded-control bg-smu-cream px-3 py-2">
                <span className="text-xs font-semibold tracking-[0.5px] text-muted uppercase">{label}</span>
                <span aria-label={`${label} price`} className="text-sm font-bold text-smu-navy">
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
