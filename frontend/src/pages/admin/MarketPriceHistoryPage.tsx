import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getMarketPriceHistory, getMarketTrades, type MarketTrade, type MarketTradePage, type PriceHistory } from '../../api/ledgerApi'
import AppLayout from '../../components/layout/AppLayout'
import MarketTradeTable from '../../components/markets/MarketTradeTable'
import PriceHistoryChart from '../../components/markets/PriceHistoryChart'
import BackLink from '../../components/ui/BackLink'
import Button from '../../components/ui/Button'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import SectionCard from '../../components/ui/SectionCard'
import { useMarketDetail } from '../../hooks/useMarketDetail'
import { usePagedList } from '../../hooks/usePagedList'

const pagedTradeOptions = {
  itemsOf: (page: MarketTradePage) => page.trades,
  nextCursorOf: (page: MarketTradePage) => page.next_cursor,
  idOf: (trade: MarketTrade) => trade.transaction_id,
}

// [2.4] #8, against Ihsan's #63 contract (sent ahead of docs/api/ landing
// there — see ledgerApi.ts).
//
// Not scoped to the market's creator: oversight is the point (#8's story is
// "as an admin... spot manipulation" on any market), so this reads the
// public detail the same way DecideOutcomePage does, not getMyMarket.
export default function MarketPriceHistoryPage() {
  const { id } = useParams<{ id: string }>()

  const { market, hasError: marketError, outcomeLabels } = useMarketDetail(id)

  const [history, setHistory] = useState<PriceHistory | null>(null)
  const [pointsLoading, setPointsLoading] = useState(true)
  const [pointsError, setPointsError] = useState(false)
  // Prices move with every trade, so the chart is only ever as good as the
  // moment it was read (#361): say when that was, and let the admin read it
  // again rather than resolve against a screen left open through a burst.
  const [readAt, setReadAt] = useState<Date | null>(null)
  const [readCount, setReadCount] = useState(0)

  useEffect(() => {
    if (!id) return
    let ignored = false
    getMarketPriceHistory(id)
      .then(found => {
        if (ignored) return
        setHistory(found)
        setReadAt(new Date())
      })
      .catch(() => { if (!ignored) setPointsError(true) })
      .finally(() => { if (!ignored) setPointsLoading(false) })
    return () => { ignored = true }
  }, [id, readCount])

  // The read is asked for here rather than in the effect, so the button is
  // disabled and any earlier failure cleared from the click that caused it.
  function handleRefresh() {
    setPointsLoading(true)
    setPointsError(false)
    setReadCount(count => count + 1)
  }

  const fetchTrades = useCallback(
    (cursor: string | undefined) => getMarketTrades(id ?? '', { cursor }),
    // readCount is deliberately unused inside: a new identity is what makes
    // usePagedList read the list again from page one, so Refresh reaches the
    // trade log as well as the chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, readCount],
  )
  const { items: trades, hasMore, isLoading: tradesLoading, hasError: tradesError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchTrades, pagedTradeOptions)

  const winningOutcomeId = history?.winning_outcome_id ?? null
  const winnerLabel = winningOutcomeId === null ? null : outcomeLabels.get(winningOutcomeId) ?? winningOutcomeId

  return (
    <AppLayout width="max-w-[1100px]">
      <BackLink to="/admin/markets" label="All Markets" className="mb-4 block" />

      {marketError && <p role="alert" className="text-sm text-danger">Failed to load this market.</p>}

      {market && (
        <>
          <PageTitle className="mb-4">{market.question}</PageTitle>

          <div className="mb-8 flex flex-wrap items-center gap-3">
            <p className="text-sm text-muted">
              {readAt === null ? 'Not read yet.' : `Read at ${readAt.toLocaleTimeString('en-SG')}`}
            </p>
            {/* Disabled while a read is on its way, so a double click asks once. */}
            <Button variant="outline" size="xs" onClick={handleRefresh} disabled={pointsLoading}>
              {pointsLoading ? 'Refreshing…' : 'Refresh'}
            </Button>
          </div>

          <SectionCard title="Price History" className="mb-6">
            {pointsLoading && history === null && <p className="text-sm text-muted">Loading price history…</p>}
            {pointsError && <p role="alert" className="text-sm text-danger">Failed to load price history.</p>}
            {history && (
              <>
                {history.result === 'settled' && (
                  <p className="mb-4 text-sm text-muted">
                    This market settled on {new Date(history.result_recorded_at ?? '').toLocaleString('en-SG')}
                    {winnerLabel === null ? '' : `; ${winnerLabel} won`}.
                  </p>
                )}
                <PriceHistoryChart outcomes={market.outcomes} points={history.points} />
              </>
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
