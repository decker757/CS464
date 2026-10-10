import { useEffect, useRef, useState } from 'react'

interface PagedList<Item, Page> {
  /** Every item loaded so far, in the server's order, each one once. */
  items: Item[]
  /**
   * The last page that arrived for this list or an earlier one, for what a page
   * carries beside its rows (the admin overview's counts). Kept while the list
   * starts again, so those do not blank out while page one is on its way.
   */
  latestPage: Page | null
  hasMore: boolean
  isLoading: boolean
  hasError: boolean
  isLoadingMore: boolean
  loadMoreFailed: boolean
  loadMore: () => void
}

// An item a later page repeats stays where it was first shown. The admin
// overview can repeat one: a draft edited, or a market settled, between two
// page reads (DECISIONS.md, "The admin overview pages by keyset, settled markets
// last, …"). A list that never repeats an item, like the public browse or the
// ledger history, is unaffected either way.
function appendUnseen<Item>(shown: Item[], page: Item[], idOf: (item: Item) => string): Item[] {
  const shownIds = new Set(shown.map(idOf))
  return [...shown, ...page.filter(item => !shownIds.has(idOf(item)))]
}

interface PagedListOptions<Item, Page> {
  /** The page's rows, in the order to render them. */
  itemsOf: (page: Page) => Item[]
  /** Pass back as `cursor` for the next page; null on the last page. */
  nextCursorOf: (page: Page) => string | null
  /** What makes two items the same one, for appendUnseen. */
  idOf: (item: Item) => string
}

// A keyset-paged list behind a "Load more" button ([X-1] #104, [T-5] #53).
// Page one is fetched on mount, and again from scratch whenever `fetchPage`
// changes, so give it a stable identity: a module-level function, or
// `useCallback` over whatever it filters by. Each later page is appended,
// never re-sorted: the server's order is the order.
//
// `fetchPage`'s own response shape (`Page`) is kept as `latestPage` rather
// than normalised, so a caller that needs more than the rows — the admin
// overview's per-status counts — still has it; `itemsOf`/`nextCursorOf` only
// say where in that shape the rows and the cursor are.
export function usePagedList<Item, Page>(
  fetchPage: (cursor: string | undefined) => Promise<Page>,
  { itemsOf, nextCursorOf, idOf }: PagedListOptions<Item, Page>,
): PagedList<Item, Page> {
  const [items, setItems] = useState<Item[]>([])
  const [latestPage, setLatestPage] = useState<Page | null>(null)
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
    setItems([])
    setNextCursor(null)
    setIsLoading(true)
    setHasError(false)
    setIsLoadingMore(false)
    setLoadMoreFailed(false)
    fetchPage(undefined)
      .then(page => {
        if (!isCurrent()) return
        setItems(itemsOf(page))
        setLatestPage(page)
        setNextCursor(nextCursorOf(page))
      })
      .catch(() => { if (isCurrent()) setHasError(true) })
      .finally(() => { if (isCurrent()) setIsLoading(false) })
    return () => { listVersion.current += 1 }
    // itemsOf/nextCursorOf/idOf are plain field accessors, not filter state —
    // unlike fetchPage, a new identity for one of them each render is not a
    // reason to restart the list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
        setItems(previous => appendUnseen(previous, itemsOf(page), idOf))
        setLatestPage(page)
        setNextCursor(nextCursorOf(page))
      })
      // The rows already shown stay; so does the button, so the user can retry.
      .catch(() => { if (isCurrent()) setLoadMoreFailed(true) })
      .finally(() => { if (isCurrent()) setIsLoadingMore(false) })
  }

  return { items, latestPage, hasMore: nextCursor !== null, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore }
}
