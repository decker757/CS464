import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getMarketPriceHistory, getMarketTrades, type MarketTrade, type PricePoint } from '../../api/ledgerApi'
import { getMarket, type PublicMarketDetail } from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import MarketTradeTable from '../../components/markets/MarketTradeTable'
import PriceHistoryChart from '../../components/markets/PriceHistoryChart'
import BackLink from '../../components/ui/BackLink'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import SectionCard from '../../components/ui/SectionCard'
import { usePagedList } from '../../hooks/usePagedList'

const pagedTradeOptions = {
  itemsOf: (page: { trades: MarketTrade[]; next_cursor: string | null }) => page.trades,
  nextCursorOf: (page: { trades: MarketTrade[]; next_cursor: string | null }) => page.next_cursor,
  idOf: (trade: MarketTrade) => trade.id,
}

// [2.4] #8. The backend slice (#63) has not shipped yet, so the price-history
// and trade-log calls in ledgerApi.ts are inferred from this issue's own
// acceptance criteria — not read off docs/api/, which has nothing for either
// yet. See ledgerApi.ts for what is assumed and reconcile it once #63 lands.
//
// Not scoped to the market's creator: oversight is the point (#8's story is
// "as an admin... spot manipulation" on any market), so this reads the
// public detail the same way DecideOutcomePage does, not getMyMarket.
export default function MarketPriceHistoryPage() {
  const { id } = useParams<{ id: string }>()

  const [market, setMarket] = useState<PublicMarketDetail | null>(null)
  const [marketError, setMarketError] = useState(false)

  const [points, setPoints] = useState<PricePoint[]>([])
  const [pointsLoading, setPointsLoading] = useState(true)
  const [pointsError, setPointsError] = useState(false)

  useEffect(() => {
    if (!id) return
    let ignored = false
    getMarket(id)
      .then(found => { if (!ignored) setMarket(found) })
      .catch(() => { if (!ignored) setMarketError(true) })
    return () => { ignored = true }
  }, [id])

  useEffect(() => {
    if (!id) return
    let ignored = false
    getMarketPriceHistory(id)
      .then(history => { if (!ignored) setPoints(history.points) })
      .catch(() => { if (!ignored) setPointsError(true) })
      .finally(() => { if (!ignored) setPointsLoading(false) })
    return () => { ignored = true }
  }, [id])

  const fetchTrades = useCallback((cursor: string | undefined) => getMarketTrades(id ?? '', { cursor }), [id])
  const { items: trades, hasMore, isLoading: tradesLoading, hasError: tradesError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchTrades, pagedTradeOptions)

  const outcomeLabels = new Map((market?.outcomes ?? []).map(outcome => [outcome.id, outcome.label]))

  return (
    <AppLayout width="max-w-[1100px]">
      <BackLink to="/admin/markets" label="All Markets" className="mb-4 block" />

      {marketError && <p role="alert" className="text-sm text-danger">Failed to load this market.</p>}

      {market && (
        <>
          <PageTitle className="mb-8">{market.question}</PageTitle>

          <SectionCard title="Price History" className="mb-6">
            {pointsLoading && <p className="text-sm text-muted">Loading price history…</p>}
            {pointsError && <p role="alert" className="text-sm text-danger">Failed to load price history.</p>}
            {!pointsLoading && !pointsError && (
              <PriceHistoryChart outcomes={market.outcomes} points={points} />
            )}
          </SectionCard>

          <h2 className="mb-4 text-[15px] font-bold text-smu-navy">Trade Log</h2>

          {tradesLoading && <p className="text-sm text-muted">Loading trades…</p>}
          {tradesError && <p role="alert" className="text-sm text-danger">Failed to load trades. Please try again.</p>}
          {!tradesLoading && !tradesError && trades.length === 0 && (
            <p className="text-sm text-muted">No trades on this market yet.</p>
          )}
          {!tradesLoading && !tradesError && trades.length > 0 && (
            <>
              <MarketTradeTable trades={trades} outcomeLabels={outcomeLabels} />
              {hasMore && (
                <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="trades" onLoadMore={loadMore} />
              )}
            </>
          )}
        </>
      )}
    </AppLayout>
  )
}
