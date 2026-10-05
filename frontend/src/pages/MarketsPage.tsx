import { useEffect, useMemo, useState } from 'react'
import { listMarkets, type PublicMarketSummary } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import MarketCard from '../components/markets/MarketCard'
import Button from '../components/ui/Button'
import PageTitle from '../components/ui/PageTitle'
import { usePricesForMarkets } from '../hooks/usePricesForMarkets'

export default function MarketsPage() {
  const [markets, setMarkets] = useState<PublicMarketSummary[]>([])
  // Where the next page starts; null once the server says there is none ([X-1] #104).
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loadMoreFailed, setLoadMoreFailed] = useState(false)
  // Memoised so the prices are fetched when the list changes, not on every render.
  const marketIds = useMemo(() => markets.map(market => market.id), [markets])
  const pricesByMarket = usePricesForMarkets(marketIds)

  useEffect(() => {
    // StrictMode mounts twice; only the second mount's answer is kept, so a
    // late first answer cannot replace a list that Load more has added to.
    let ignored = false
    listMarkets()
      .then(page => {
        if (ignored) return
        setMarkets(page.markets)
        setNextCursor(page.nextCursor)
      })
      .catch(() => { if (!ignored) setError(true) })
      .finally(() => { if (!ignored) setLoading(false) })
    return () => { ignored = true }
  }, [])

  function handleLoadMore() {
    if (!nextCursor) return
    setIsLoadingMore(true)
    setLoadMoreFailed(false)
    listMarkets({ cursor: nextCursor })
      .then(page => {
        // Appended, never re-sorted: the server's order is the order (market-service.md).
        setMarkets(previous => [...previous, ...page.markets])
        setNextCursor(page.nextCursor)
      })
      // The cards already shown stay; the button stays too, so the trader can retry.
      .catch(() => setLoadMoreFailed(true))
      .finally(() => setIsLoadingMore(false))
  }

  return (
    <AppLayout width="max-w-[1200px]">
      <PageTitle className="mb-8">Markets</PageTitle>

      {loading && <p className="text-sm text-muted">Loading markets…</p>}

      {error && <p role="alert" className="text-sm text-danger">Failed to load markets. Please try again.</p>}

      {!loading && !error && markets.length === 0 && (
        <p className="text-sm text-muted">No markets available right now.</p>
      )}

      {!loading && !error && markets.length > 0 && (
        <>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4">
            {markets.map((market) => <MarketCard key={market.id} market={market} prices={pricesByMarket.get(market.id)} />)}
          </div>

          {loadMoreFailed && (
            <p role="alert" className="mt-6 text-sm text-danger">Couldn't load more markets. Please try again.</p>
          )}

          {nextCursor && (
            <div className="mt-8 flex justify-center">
              {/* Disabled while a page is on its way, so a double click asks for it once (#104). */}
              <Button variant="outline" size="md" onClick={handleLoadMore} disabled={isLoadingMore}>
                {isLoadingMore ? 'Loading…' : 'Load more'}
              </Button>
            </div>
          )}
        </>
      )}
    </AppLayout>
  )
}
