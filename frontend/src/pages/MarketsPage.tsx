import { useMemo } from 'react'
import { listMarkets, type PublicMarketPage, type PublicMarketSummary } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import MarketCard from '../components/markets/MarketCard'
import LoadMoreControl from '../components/ui/LoadMoreControl'
import PageTitle from '../components/ui/PageTitle'
import { usePagedList } from '../hooks/usePagedList'
import { usePricesForMarkets } from '../hooks/usePricesForMarkets'

// Module-level, so it keeps its identity and the list is fetched once ([X-1] #104).
function fetchBrowsePage(cursor: string | undefined) {
  return listMarkets({ cursor })
}

const pagedMarketsOptions = {
  itemsOf: (page: PublicMarketPage) => page.markets,
  nextCursorOf: (page: PublicMarketPage) => page.nextCursor,
  idOf: (market: PublicMarketSummary) => market.id,
}

export default function MarketsPage() {
  const { items: markets, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchBrowsePage, pagedMarketsOptions)
  // Memoised so the prices are fetched when the list changes, not on every render.
  const marketIds = useMemo(() => markets.map(market => market.id), [markets])
  const pricesByMarket = usePricesForMarkets(marketIds)

  return (
    <AppLayout width="max-w-[1200px]">
      <PageTitle className="mb-8">Markets</PageTitle>

      {isLoading && <p className="text-sm text-muted">Loading markets…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load markets. Please try again.</p>}

      {!isLoading && !hasError && markets.length === 0 && (
        <p className="text-sm text-muted">No markets available right now.</p>
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
    </AppLayout>
  )
}
