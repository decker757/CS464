import { Link } from 'react-router-dom'
import type { PublicMarketSummary } from '../../api/marketApi'
import Card from '../ui/Card'
import { formatCloseTime } from './marketStatus'
import StatusBadge from './StatusBadge'

export default function MarketCard({ market }: { market: PublicMarketSummary }) {
  return (
    <Link to={`/markets/${market.id}`} className="block">
      <Card interactive className="h-full cursor-pointer px-6 py-5">
        <div className="mb-3 flex items-center justify-between gap-2">
          <StatusBadge status={market.status} />
          <span className="text-right text-xs text-subtle">{formatCloseTime(market.status, market.close_time)}</span>
        </div>
        <p className="text-[15px] leading-normal font-semibold text-smu-navy">{market.question}</p>
      </Card>
    </Link>
  )
}
