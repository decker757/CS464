import { useMemo } from 'react'
import { listMarkets } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import LoadMoreMarkets from '../components/markets/LoadMoreMarkets'
import MarketCard from '../components/markets/MarketCard'
import PageTitle from '../components/ui/PageTitle'
import { usePagedMarkets } from '../hooks/usePagedMarkets'
import { usePricesForMarkets } from '../hooks/usePricesForMarkets'

// Module-level, so it keeps its identity and the list is fetched once ([X-1] #104).
function fetchBrowsePage(cursor: string | undefined) {
  return listMarkets({ cursor })
}

export default function MarketsPage() {
  const { markets, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedMarkets(fetchBrowsePage)
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
            <LoadMoreMarkets isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} onLoadMore={loadMore} />
          )}
        </>
      )}
    </AppLayout>
  )
}
