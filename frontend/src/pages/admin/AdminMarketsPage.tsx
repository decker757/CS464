import { useCallback, useState } from 'react'
import { getMarketOverview, type MarketOverview } from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import PageTitle from '../../components/ui/PageTitle'
import { selectableClass } from '../../components/ui/selectableClass'
import AdminMarketRow from '../../components/markets/AdminMarketRow'
import LoadMoreMarkets from '../../components/markets/LoadMoreMarkets'
import { STATUS_CONFIG, type AdminMarketStatus } from '../../components/markets/marketStatus'
import { useAuth } from '../../context/AuthContext'
import { usePagedMarkets } from '../../hooks/usePagedMarkets'

type StatusFilter = AdminMarketStatus | 'all'
type StatusCounts = MarketOverview['counts']

const FILTERS: StatusFilter[] = ['all', 'draft', 'submitted', 'open', 'closed', 'pending_resolution', 'approved']

function filterLabel(filter: StatusFilter): string {
  return filter === 'all' ? 'All' : STATUS_CONFIG[filter].label
}

// A tab's number. The counts cover every market the admin can see, so All is
// their sum, never the rows on screen, which are only the pages loaded so far.
function countFor(filter: StatusFilter, counts: StatusCounts | undefined): number {
  if (counts === undefined) return 0
  if (filter !== 'all') return counts[filter] ?? 0
  let total = 0
  for (const count of Object.values(counts)) total += count
  return total
}

// [2.1] #5, #235: the server decides which markets an admin may see, their
// status (from the clock, so a market past its close time is already closed),
// the order, the filter and the counts. The page asks for one status and one
// page at a time, and shows what it is given.
export default function AdminMarketsPage() {
  const { user } = useAuth()
  const [filter, setFilter] = useState<StatusFilter>('all')
  // A new filter is a new list from page one: a cursor continues only the list
  // it came from, so the rows and the cursor are cleared, and a page still on
  // its way for the old filter is dropped when it arrives (usePagedMarkets).
  const fetchPage = useCallback(
    (cursor: string | undefined) =>
      getMarketOverview({ status: filter === 'all' ? undefined : filter, cursor }),
    [filter],
  )
  const { markets, latestPage, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedMarkets(fetchPage)
  // The latest response's counts, Load more's included.
  const counts = latestPage?.counts

  return (
    <AppLayout width="max-w-[1000px]">
      <PageTitle className="mb-8">All Markets</PageTitle>

      <div role="tablist" aria-label="Filter by status" className="mb-6 flex flex-wrap gap-2">
        {FILTERS.map(f => {
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
              {filterLabel(f)} <span className={active ? 'text-white/70' : 'text-subtle'}>{countFor(f, counts)}</span>
            </button>
          )
        })}
      </div>

      {isLoading && <p className="text-sm text-muted">Loading markets…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load markets. Please try again.</p>}

      {!isLoading && !hasError && markets.length === 0 && (
        <p className="text-sm text-muted">No markets in this status.</p>
      )}

      {!isLoading && !hasError && markets.length > 0 && (
        <>
          <div className="flex flex-col gap-3">
            {markets.map(market => (
              <AdminMarketRow key={market.id} market={market} isMine={market.creator_id === user?.id} />
            ))}
          </div>

          {/* A failed Load more leaves the cursor as it was, so the alert never needs it null. */}
          {hasMore && (
            <LoadMoreMarkets isLoading={isLoadingMore} hasFailed={loadMoreFailed} onLoadMore={loadMore} />
          )}
        </>
      )}
    </AppLayout>
  )
}
