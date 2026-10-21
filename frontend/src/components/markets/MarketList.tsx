import { useCallback, useState } from 'react'
import { listMarkets, type PublicMarketPage, type PublicMarketSummary } from '../../api/marketApi'
import { usePagedList } from '../../hooks/usePagedList'
import { usePricesForMarkets } from '../../hooks/usePricesForMarkets'
import Button from '../ui/Button'
import LoadMoreControl from '../ui/LoadMoreControl'
import MarketCard from './MarketCard'
import type { StatusFilter } from './MarketFilters'

interface MarketListProps {
  /** Empty for no search. */
  query: string
  status: StatusFilter | null
}

const pagedMarketsOptions = {
  itemsOf: (page: PublicMarketPage) => page.markets,
  nextCursorOf: (page: PublicMarketPage) => page.nextCursor,
  idOf: (market: PublicMarketSummary) => market.id,
}

// One search and one status, from its first page on. A different search or
// status is a different list: the page gives this a new `key`, so the paging
// state and the prices start over (usePricesForMarkets says to do exactly that).
export default function MarketList({ query, status }: MarketListProps) {
  // Bumped by Try again, and nothing else. usePagedList starts its list over
  // whenever `fetchPage` changes identity, so that is the whole retry: page one
  // again, under the same search and status, with any failed attempt dropped.
  const [attempt, setAttempt] = useState(0)
  const fetchMarketPage = useCallback(
    (cursor: string | undefined) => listMarkets({ cursor, q: query || undefined, status: status ?? undefined }),
    // `attempt` is not read in there, and is the point: a new identity for
    // this function is what asks usePagedList for page one again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [query, status, attempt],
  )
  const { items: markets, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchMarketPage, pagedMarketsOptions)
  // `markets` keeps its identity until the list itself changes, so the prices
  // are fetched then and not on every render.
  const { snapshotsByMarket, failedMarkets, readAt, retryMarket } = usePricesForMarkets(markets)
  const isFiltered = query !== '' || status !== null

  return (
    <>
      {isLoading && <p className="text-sm text-muted">Loading markets…</p>}

      {hasError && (
        <div className="flex flex-wrap items-center gap-3">
          <p role="alert" className="text-sm text-danger">Failed to load markets.</p>
          <Button variant="outline" size="sm" onClick={() => setAttempt(previous => previous + 1)}>Try again</Button>
        </div>
      )}

      {!isLoading && !hasError && markets.length === 0 && (
        <p className="text-sm text-muted">
          {isFiltered ? 'No markets match these filters.' : 'No markets available right now.'}
        </p>
      )}

      {!isLoading && !hasError && markets.length > 0 && (
        <>
          {/* The prices refresh on their own; this says when the last one
              landed, so a refresh that is failing shows as a time going stale
              rather than as a number that looks current. */}
          {readAt && (
            <p className="mb-3 text-right text-xs text-subtle">
              Prices updated {readAt.toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit' })}
            </p>
          )}

          <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4">
            {markets.map(market => (
              <MarketCard
                key={market.id}
                market={market}
                snapshot={snapshotsByMarket.get(market.id)}
                priceReadFailed={failedMarkets.has(market.id)}
                onRetryPrice={() => retryMarket(market.id)}
              />
            ))}
          </div>

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="markets" onLoadMore={loadMore} />
          )}
        </>
      )}
    </>
  )
}
