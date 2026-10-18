import { getLeaderboard, type LeaderboardPage as LeaderboardApiPage, type LeaderboardRow } from '../api/ledgerApi'
import { errorCode } from '../api/errors'
import { usePagedList } from './usePagedList'

interface LeaderboardState {
  rows: LeaderboardRow[]
  me: LeaderboardApiPage['me']
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

// A module-level function, so usePagedList's mount effect (keyed on this
// identity) does not see a new one every render and restart the list.
function fetchLeaderboardPage(cursor: string | undefined) {
  return getLeaderboard(cursor === undefined ? undefined : { cursor })
}

const leaderboardPagedListOptions = {
  itemsOf: (page: LeaderboardApiPage) => page.rows,
  nextCursorOf: (page: LeaderboardApiPage) => page.next_cursor,
  idOf: (row: LeaderboardRow) => String(row.rank),
  // The one genuine fork from every other usePagedList caller:
  // `409 leaderboard_refreshed` (ledger-service.md) restarts the whole list
  // from page one of the new ranking rather than leaving a failed
  // "Load more".
  restartOnLoadMoreError: (err: unknown) => errorCode(err) === 'leaderboard_refreshed',
}

export function useLeaderboard(): LeaderboardState {
  const { items, latestPage, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(
    fetchLeaderboardPage,
    leaderboardPagedListOptions,
  )

  return {
    rows: items,
    me: latestPage?.me ?? null,
    computedAt: latestPage?.computed_at ?? null,
    stale: latestPage?.stale ?? false,
    rankedUsers: latestPage?.ranked_users ?? 0,
    hasMore,
    isLoading,
    hasError,
    isLoadingMore,
    loadMoreFailed,
    loadMore,
  }
}
