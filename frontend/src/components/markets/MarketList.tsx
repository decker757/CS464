import { useCallback, useMemo } from 'react'
import { listMarkets, type PublicMarketPage, type PublicMarketSummary } from '../../api/marketApi'
import { usePagedList } from '../../hooks/usePagedList'
import { usePricesForMarkets } from '../../hooks/usePricesForMarkets'
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
  const fetchMarketPage = useCallback(
    (cursor: string | undefined) => listMarkets({ cursor, q: query || undefined, status: status ?? undefined }),
    [query, status],
  )
  const { items: markets, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchMarketPage, pagedMarketsOptions)
  // Memoised so the prices are fetched when the list changes, not on every render.
  const marketIds = useMemo(() => markets.map(market => market.id), [markets])
  const pricesByMarket = usePricesForMarkets(marketIds)
  const isFiltered = query !== '' || status !== null

  return (
    <>
      {isLoading && <p className="text-sm text-muted">Loading markets…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load markets. Please try again.</p>}

      {!isLoading && !hasError && markets.length === 0 && (
        <p className="text-sm text-muted">
          {isFiltered ? 'No markets match these filters.' : 'No markets available right now.'}
        </p>
      )}

      {!isLoading && !hasError && markets.length > 0 && (
        <>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4">
            {markets.map((market) => <MarketCard key={market.id} market={market} prices={pricesByMarket.get(market.id)} />)}
          </div>

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="markets" onLoadMore={loadMore} />
          )}
        </>
      )}
    </>
  )
}
