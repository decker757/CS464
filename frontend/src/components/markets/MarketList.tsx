import { useCallback, useState } from 'react'
import { listMarkets, type PublicMarketPage, type PublicMarketSummary } from '../../api/marketApi'
import { closingWindowOf, type ClosingPreset } from '../../utils/closingWindow'
import { usePagedList } from '../../hooks/usePagedList'
import { useMyPositions } from '../../hooks/useMyPositions'
import { usePricesForMarkets } from '../../hooks/usePricesForMarkets'
import Button from '../ui/Button'
import LoadMoreControl from '../ui/LoadMoreControl'
import MarketBand from './MarketBand'
import MarketCardGrid from './MarketCardGrid'
import PriceFeedStatus from './PriceFeedStatus'
import { browseSectionsOf, featuredMarketOf } from './browseSections'
import type { StatusFilter } from './MarketFilters'

interface MarketListProps {
  /** Empty for no search. */
  query: string
  status: StatusFilter | null
  /** null for any closing time (#442). */
  closing: ClosingPreset | null
}

const pagedMarketsOptions = {
  itemsOf: (page: PublicMarketPage) => page.markets,
  nextCursorOf: (page: PublicMarketPage) => page.nextCursor,
  idOf: (market: PublicMarketSummary) => market.id,
}

// One search, one status and one closing preset, from its first page on. A
// different one of any is a different list: the page gives this a new `key`,
// so the paging state and the prices start over (usePricesForMarkets says to
// do exactly that).
export default function MarketList({ query, status, closing }: MarketListProps) {
  // The preset's window as of the moment this list started, and kept for its
  // life: every page asks for the same window even if a midnight passes while
  // the trader pages, and a list started after midnight, by a new search or a
  // new choice, asks for the new day (#442).
  const [closingWindow] = useState(() => (closing === null ? null : closingWindowOf(closing, new Date())))
  // Bumped by Try again, and nothing else. usePagedList starts its list over
  // whenever `fetchPage` changes identity, so that is the whole retry: page one
  // again, under the same search, status and window, with any failed attempt
  // dropped.
  const [attempt, setAttempt] = useState(0)
  const fetchMarketPage = useCallback(
    (cursor: string | undefined) =>
      listMarkets({
        cursor,
        q: query || undefined,
        status: status ?? undefined,
        closes_from: closingWindow?.closesFrom,
        closes_before: closingWindow?.closesBefore,
      }),
    // `attempt` is not read in there, and is the point: a new identity for
    // this function is what asks usePagedList for page one again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [query, status, closingWindow, attempt],
  )
  const { items: markets, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchMarketPage, pagedMarketsOptions)
  // `markets` keeps its identity until the list itself changes, so the prices
  // are fetched then and not on every render.
  const { snapshotsByMarket, failedMarkets, readAt, retryMarket, status: priceStatus, marketFigures } = usePricesForMarkets(markets)
  // One portfolio read for the page, not one per card ([X-1] #450).
  const { positionsByMarket } = useMyPositions()
  const isFiltered = query !== '' || status !== null || closingWindow !== null
  // The hero and the bands, for the unfiltered view only: a search or a filter
  // collapses back to a single plain result list, where a band would be
  // answering a question the trader did not ask (#448 AC 4).
  //
  // Computed from the markets already loaded and the prices already read, so a
  // band costs no request of its own. That also means a band describes the
  // pages fetched so far, which is the honest reading of "trending" on a list
  // the trader is still paging through.
  //
  // The bands are given the hero to leave out: it is the busiest market, so it
  // would otherwise lead Trending as well and sit above the list twice.
  const featured = isFiltered ? null : featuredMarketOf(markets, marketFigures, new Date())
  const sections = isFiltered
    ? []
    : browseSectionsOf(markets, snapshotsByMarket, marketFigures, new Date(), featured?.market.id)
  const hasBandsAbove = featured !== null || sections.length > 0
  // Everything every band hands its cards, which differs only by market.
  const cardProps = { snapshotsByMarket, failedMarkets, positionsByMarket, onRetryPrice: retryMarket }

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
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            {/* The feed's own trouble, when it has any ([X-1] #444 AC 5). A
                frozen list that looks live is the state this prevents. */}
            <PriceFeedStatus status={priceStatus} />
            {readAt && (
              <p className="ml-auto text-right text-xs text-subtle">
                Prices updated {readAt.toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit' })}
              </p>
            )}
          </div>

          {/* One featured market at the top, when a market has earned it:
              the busiest the page has loaded. Rendered through the same card
              as everything else, in a band of one, so a featured market is
              emphasis rather than a second design to maintain. */}
          {featured && (
            <MarketBand
              id="featured"
              title="Featured"
              rule={featured.rule}
              markets={[featured.market]}
              {...cardProps}
            />
          )}

          {/* The curated bands, above the full list and only when nothing is
              filtered (#448 AC 4). Each market in a band is still in the list
              below: a band is a shortcut into the list, not a partition of
              it (AC 5). */}
          {sections.map(section => (
            <MarketBand
              key={section.id}
              id={section.id}
              title={section.title}
              rule={section.rule}
              markets={section.markets}
              {...cardProps}
            />
          ))}

          {/* The full list. It takes a heading only when something sits above
              it: an unsectioned page is not given a heading it never had. */}
          {hasBandsAbove ? (
            <MarketBand id="all" title="Browse all" markets={markets} {...cardProps} />
          ) : (
            <MarketCardGrid markets={markets} {...cardProps} />
          )}

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="markets" onLoadMore={loadMore} />
          )}
        </>
      )}
    </>
  )
}
