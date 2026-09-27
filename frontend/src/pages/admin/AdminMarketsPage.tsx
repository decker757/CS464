import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { listMyMarkets, type MarketSummaryOut } from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import Card from '../../components/ui/Card'
import { buttonClass } from '../../components/ui/buttonClass'
import StatusBadge from '../../components/markets/StatusBadge'
import { STATUS_CONFIG, tradingStopped, type AdminMarketStatus } from '../../components/markets/marketStatus'

type StatusFilter = AdminMarketStatus | 'all'

const FILTERS: StatusFilter[] = ['all', 'draft', 'submitted', 'open', 'closed', 'pending_resolution', 'approved']

function filterLabel(filter: StatusFilter): string {
  return filter === 'all' ? 'All' : STATUS_CONFIG[filter].label
}

// Markets with no close_time (draft, submitted) sort after every market that
// has one, oldest-scheduled-close first for the rest — the soonest closing
// market is the one that needs an admin's attention soonest.
function byClosingSoonest(a: MarketSummaryOut, b: MarketSummaryOut): number {
  if (!a.close_time && !b.close_time) return 0
  if (!a.close_time) return 1
  if (!b.close_time) return -1
  return new Date(a.close_time).getTime() - new Date(b.close_time).getTime()
}

function countByStatus(markets: MarketSummaryOut[]): Record<AdminMarketStatus, number> {
  const counts = { draft: 0, submitted: 0, open: 0, closed: 0, pending_resolution: 0, approved: 0 }
  for (const market of markets) counts[market.status]++
  return counts
}

// "Closes {date}" only while the market can still trade — a market closed
// early keeps a future close_time (ADR 0014), and a market past open (pending
// resolution, approved) has a close_time that is no longer a live deadline.
function formatCloseTime(market: MarketSummaryOut): string {
  if (!market.close_time) return '—'
  if (tradingStopped(market.status, market.close_time)) return 'Closed'
  return `Closes ${new Date(market.close_time).toLocaleString('en-SG', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}`
}

export default function AdminMarketsPage() {
  const [markets, setMarkets] = useState<MarketSummaryOut[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [filter, setFilter] = useState<StatusFilter>('all')

  useEffect(() => {
    listMyMarkets()
      .then(setMarkets)
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
      <h1 className="mb-8 text-[28px] font-extrabold tracking-[-0.3px] text-smu-navy">My Markets</h1>

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
                <p className="mt-1 text-xs text-subtle">{formatCloseTime(market)}</p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {market.status === 'closed' && (
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
