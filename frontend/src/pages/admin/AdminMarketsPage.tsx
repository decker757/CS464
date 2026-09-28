import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { listMarkets, listMyMarkets, type MarketSummaryOut, type PublicMarketSummary } from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import Card from '../../components/ui/Card'
import PageTitle from '../../components/ui/PageTitle'
import { buttonClass } from '../../components/ui/buttonClass'
import StatusBadge from '../../components/markets/StatusBadge'
import { STATUS_CONFIG, formatCloseTime, type AdminMarketStatus } from '../../components/markets/marketStatus'

// A market on the list, and whether the signed-in admin created it: only the
// creator can act on it, e.g. propose its outcome.
type MarketRow = Pick<MarketSummaryOut, 'id' | 'status' | 'question' | 'close_time'> & { isMine: boolean }

type StatusFilter = AdminMarketStatus | 'all'

const FILTERS: StatusFilter[] = ['all', 'draft', 'submitted', 'open', 'closed', 'pending_resolution', 'approved']

function filterLabel(filter: StatusFilter): string {
  return filter === 'all' ? 'All' : STATUS_CONFIG[filter].label
}

// Markets with no close_time (draft, submitted) sort after every market that
// has one, oldest-scheduled-close first for the rest — the soonest closing
// market is the one that needs an admin's attention soonest.
function byClosingSoonest(a: MarketRow, b: MarketRow): number {
  if (!a.close_time && !b.close_time) return 0
  if (!a.close_time) return 1
  if (!b.close_time) return -1
  return new Date(a.close_time).getTime() - new Date(b.close_time).getTime()
}

// [2.1] #5 asks for all markets. GET /markets returns the admin's own in every
// status, and GET /public/markets every admin's once published, so together
// they are all an admin may see; another admin's draft stays private. The
// admin's own markets are in both lists and are kept once, from the first.
function combineMarkets(mine: MarketSummaryOut[], published: PublicMarketSummary[]): MarketRow[] {
  const rows: MarketRow[] = mine.map(market => ({ ...market, isMine: true }))
  const myIds = new Set(mine.map(market => market.id))
  for (const market of published) {
    if (!myIds.has(market.id)) rows.push({ ...market, isMine: false })
  }
  return rows
}

function countByStatus(markets: MarketRow[]): Record<AdminMarketStatus, number> {
  const counts = { draft: 0, submitted: 0, open: 0, closed: 0, pending_resolution: 0, approved: 0 }
  for (const market of markets) counts[market.status]++
  return counts
}

export default function AdminMarketsPage() {
  const [markets, setMarkets] = useState<MarketRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [filter, setFilter] = useState<StatusFilter>('all')

  useEffect(() => {
    Promise.all([listMyMarkets(), listMarkets()])
      .then(([mine, published]) => setMarkets(combineMarkets(mine, published)))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  const counts = useMemo(() => countByStatus(markets), [markets])
  const total = markets.length

  const visibleMarkets = useMemo(() => {
    const filtered = filter === 'all' ? markets : markets.filter(m => m.status === filter)
    return [...filtered].sort(byClosingSoonest)
  }, [markets, filter])

  return (
    <AppLayout width="max-w-[1000px]">
      <PageTitle className="mb-8">All Markets</PageTitle>

      <div role="tablist" aria-label="Filter by status" className="mb-6 flex flex-wrap gap-2">
        {FILTERS.map(f => {
          const count = f === 'all' ? total : counts[f]
          const active = filter === f
          return (
            <button
              key={f}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setFilter(f)}
              className={`cursor-pointer rounded-full border px-4 py-1.5 text-[13px] font-semibold transition ${
                active
                  ? 'border-smu-navy bg-smu-navy text-white'
                  : 'border-smu-navy/20 text-smu-navy hover:border-smu-navy/40'
              }`}
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
          {visibleMarkets.map(market => (
            <Card key={market.id} className="flex items-center justify-between gap-4 px-6 py-4">
              <div className="min-w-0">
                <p className="truncate text-[15px] font-semibold text-smu-navy">
                  {market.question ?? <span className="italic text-subtle">Untitled market</span>}
                </p>
                <p className="mt-1 text-xs text-subtle">
                  {formatCloseTime(market.status, market.close_time)}
                  {market.isMine && ' · Created by you'}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {market.isMine && market.status === 'closed' && (
                  <Link to={`/admin/markets/${market.id}/propose-outcome`} className={buttonClass('outline', 'xs')}>
                    Propose Outcome
                  </Link>
                )}
                <StatusBadge status={market.status} />
              </div>
            </Card>
          ))}
        </div>
      )}
    </AppLayout>
  )
}
