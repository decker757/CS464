import { useEffect, useRef, useState } from 'react'

interface MarketPage {
  markets: { id: string }[]
  /** Pass back as `cursor` for the next page; null on the last page. */
  nextCursor: string | null
}

interface PagedMarkets<Market> {
  /** Every page loaded so far, in the server's order. */
  markets: Market[]
  hasMore: boolean
  isLoading: boolean
  hasError: boolean
  isLoadingMore: boolean
  loadMoreFailed: boolean
  loadMore: () => void
}

// A keyset-paged market list behind a "Load more" button ([X-1] #104). Page one
// is fetched on mount, and again from scratch whenever `fetchPage` changes, so
// give it a stable identity: a module-level function, or `useCallback` over
// whatever it filters by. Each later page is appended, never re-sorted: the
// server's order is the order (market-service.md).
export function usePagedMarkets<Page extends MarketPage>(
  fetchPage: (cursor: string | undefined) => Promise<Page>,
): PagedMarkets<Page['markets'][number]> {
  type Market = Page['markets'][number]

  const [markets, setMarkets] = useState<Market[]>([])
  // Where the next page starts; null once the server says there is none.
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loadMoreFailed, setLoadMoreFailed] = useState(false)
  // Bumped when a list ends: `fetchPage` changed, or the page unmounted.
  // StrictMode mounts twice and ends the first list on purpose. An answer to a
  // request made for an ended list is dropped when it arrives, so a late page
  // one cannot replace a list that Load more has added to, and a page asked
  // for under an old filter cannot land under a new one.
  const listVersion = useRef(0)

  useEffect(() => {
    const version = listVersion.current
    const isCurrent = () => version === listVersion.current
    setMarkets([])
    setNextCursor(null)
    setIsLoading(true)
    setHasError(false)
    setIsLoadingMore(false)
    setLoadMoreFailed(false)
    fetchPage(undefined)
      .then(page => {
        if (!isCurrent()) return
        setMarkets(page.markets)
        setNextCursor(page.nextCursor)
      })
      .catch(() => { if (isCurrent()) setHasError(true) })
      .finally(() => { if (isCurrent()) setIsLoading(false) })
    return () => { listVersion.current += 1 }
  }, [fetchPage])

  function loadMore() {
    if (nextCursor === null) return
    const version = listVersion.current
    const isCurrent = () => version === listVersion.current
    setIsLoadingMore(true)
    setLoadMoreFailed(false)
    fetchPage(nextCursor)
      .then(page => {
        if (!isCurrent()) return
        setMarkets(previous => [...previous, ...page.markets])
        setNextCursor(page.nextCursor)
      })
      // The rows already shown stay; so does the button, so the user can retry.
      .catch(() => { if (isCurrent()) setLoadMoreFailed(true) })
      .finally(() => { if (isCurrent()) setIsLoadingMore(false) })
  }

  return { markets, hasMore: nextCursor !== null, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore }
}
