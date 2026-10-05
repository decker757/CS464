import { Link } from 'react-router-dom'
import type { MarketOverviewRow } from '../../api/marketApi'
import Card from '../ui/Card'
import { buttonClass } from '../ui/buttonClass'
import StatusBadge from './StatusBadge'
import { formatCloseTime } from './marketStatus'

interface AdminMarketRowProps {
  market: MarketOverviewRow
  isMine: boolean
}

// One market on the admin markets list ([2.1] #5). Only its creator can act on
// it, e.g. propose its outcome.
export default function AdminMarketRow({ market, isMine }: AdminMarketRowProps) {
  return (
    <Card className="flex items-center justify-between gap-4 px-6 py-4">
      <div className="min-w-0">
        <p className="truncate text-[15px] font-semibold text-smu-navy">
          {market.question ?? <span className="italic text-subtle">Untitled market</span>}
        </p>
        <p className="mt-1 text-xs text-subtle">
          {formatCloseTime(market.status, market.close_time)}
          {isMine && ' · Created by you'}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {isMine && market.status === 'closed' && (
          <Link to={`/admin/markets/${market.id}/propose-outcome`} className={buttonClass('outline', 'xs')}>
            Propose Outcome
          </Link>
        )}
        <StatusBadge status={market.status} />
      </div>
    </Card>
  )
}
