import { useEffect, useRef, useState } from 'react'
import { getLeaderboard, type LeaderboardMe, type LeaderboardRow } from '../api/ledgerApi'
import { errorCode } from '../api/errors'

interface LeaderboardState {
  rows: LeaderboardRow[]
  me: LeaderboardMe | null
  computedAt: string | null
  stale: boolean
  rankedUsers: number
  hasMore: boolean
  isLoading: boolean
  hasError: boolean
  isLoadingMore: boolean
  loadMoreFailed: boolean
  loadMore: () => void
}

// A ranked list, not a keyset-paged one like usePagedList's callers: a
// cursor here holds the caller to one ranking (ledger-service.md), and a
// refresh past that ranking answers 409 leaderboard_refreshed rather than a
// page. The required recovery is to drop everything shown and restart from
// page one of the new ranking — never append to it, never show it as a
// failed "Load more" the way every other list's 409-shaped nothing would.
// That is a real fork in what "Load more" does, not a paging detail, so this
// stays its own hook rather than a branch inside usePagedList.
export function useLeaderboard(): LeaderboardState {
  const [rows, setRows] = useState<LeaderboardRow[]>([])
  const [me, setMe] = useState<LeaderboardMe | null>(null)
  const [computedAt, setComputedAt] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  const [rankedUsers, setRankedUsers] = useState(0)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loadMoreFailed, setLoadMoreFailed] = useState(false)
  // Bumped on unmount, so a page-one answer that lands late never overwrites
  // what a faster "Load more" already appended (usePagedList's own reason).
  const listVersion = useRef(0)

  useEffect(() => {
    const version = listVersion.current
    const isCurrent = () => version === listVersion.current
    getLeaderboard()
      .then(page => {
        if (!isCurrent()) return
        setRows(page.rows)
        setMe(page.me)
        setComputedAt(page.computed_at)
        setStale(page.stale)
        setRankedUsers(page.ranked_users)
        setNextCursor(page.next_cursor)
      })
      .catch(() => { if (isCurrent()) setHasError(true) })
      .finally(() => { if (isCurrent()) setIsLoading(false) })
    return () => { listVersion.current += 1 }
  }, [])

  function loadMore() {
    if (nextCursor === null) return
    const version = listVersion.current
    const isCurrent = () => version === listVersion.current
    setIsLoadingMore(true)
    setLoadMoreFailed(false)
    getLeaderboard({ cursor: nextCursor })
      .then(page => {
        if (!isCurrent()) return
        setRows(previous => [...previous, ...page.rows])
        setMe(page.me)
        setComputedAt(page.computed_at)
        setStale(page.stale)
        setRankedUsers(page.ranked_users)
        setNextCursor(page.next_cursor)
      })
      .catch(async err => {
        if (!isCurrent()) return
        if (errorCode(err) !== 'leaderboard_refreshed') {
          setLoadMoreFailed(true)
          return
        }
        // The ranking this walk was on has been replaced twice. Restart from
        // page one of the new one and replace everything shown with it —
        // the old rows are not a partial answer, they are the wrong ranking.
        try {
          const page = await getLeaderboard()
          if (!isCurrent()) return
          setRows(page.rows)
          setMe(page.me)
          setComputedAt(page.computed_at)
          setStale(page.stale)
          setRankedUsers(page.ranked_users)
          setNextCursor(page.next_cursor)
        } catch {
          if (isCurrent()) setLoadMoreFailed(true)
        }
      })
      .finally(() => { if (isCurrent()) setIsLoadingMore(false) })
  }

  return {
    rows, me, computedAt, stale, rankedUsers,
    hasMore: nextCursor !== null,
    isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore,
  }
}
