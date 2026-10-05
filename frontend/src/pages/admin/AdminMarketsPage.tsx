import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMarketOverview, type MarketOverview } from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import Card from '../../components/ui/Card'
import PageTitle from '../../components/ui/PageTitle'
import { buttonClass } from '../../components/ui/buttonClass'
import { selectableClass } from '../../components/ui/selectableClass'
import StatusBadge from '../../components/markets/StatusBadge'
import { STATUS_CONFIG, formatCloseTime, type AdminMarketStatus } from '../../components/markets/marketStatus'
import { useAuth } from '../../context/AuthContext'

type StatusFilter = AdminMarketStatus | 'all'

const FILTERS: StatusFilter[] = ['all', 'draft', 'submitted', 'open', 'closed', 'pending_resolution', 'approved']

function filterLabel(filter: StatusFilter): string {
  return filter === 'all' ? 'All' : STATUS_CONFIG[filter].label
}

// [2.1] #5: the server decides which markets an admin may see, their status
// (from the clock, so a market past its close time is already closed), the
// order and the counts. The page only filters the rows it was given, which
// keeps the list and the counts from one response. Once #104 pages this list,
// the filter has to move to the request's `status` parameter.
export default function AdminMarketsPage() {
  const { user } = useAuth()
  const [overview, setOverview] = useState<MarketOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [filter, setFilter] = useState<StatusFilter>('all')

  useEffect(() => {
    getMarketOverview()
      .then(setOverview)
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  const markets = overview?.markets ?? []
  const total = markets.length

  const visibleMarkets = filter === 'all' ? markets : markets.filter(m => m.status === filter)

  return (
    <AppLayout width="max-w-[1000px]">
      <PageTitle className="mb-8">All Markets</PageTitle>

      <div role="tablist" aria-label="Filter by status" className="mb-6 flex flex-wrap gap-2">
        {FILTERS.map(f => {
          const count = f === 'all' ? total : (overview?.counts[f] ?? 0)
          const active = filter === f
          return (
            <button
              key={f}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setFilter(f)}
              className={`cursor-pointer rounded-full border px-4 py-1.5 text-[13px] font-semibold transition ${selectableClass(active)}`}
            >
              {filterLabel(f)} <span className={active ? 'text-white/70' : 'text-subtle'}>{count}</span>
            </button>
          )
        })}
      </div>

      {loading && <p className="text-sm text-muted">Loading markets…</p>}

      {error && <p role="alert" className="text-sm text-danger">Failed to load markets. Please try again.</p>}

      {!loading && !error && visibleMarkets.length === 0 && (
        <p className="text-sm text-muted">No markets in this status.</p>
      )}

      {!loading && !error && visibleMarkets.length > 0 && (
        <div className="flex flex-col gap-3">
          {visibleMarkets.map(market => {
            // Only the creator can act on a market, e.g. propose its outcome.
            const isMine = market.creator_id === user?.id
            return (
              <Card key={market.id} className="flex items-center justify-between gap-4 px-6 py-4">
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
          })}
        </div>
      )}
    </AppLayout>
  )
}
