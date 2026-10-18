import AppLayout from '../components/layout/AppLayout'
import { HeaderCell, NumberCell, TableFrame, TableHeaderRow } from '../components/ui/Table'
import LoadMoreControl from '../components/ui/LoadMoreControl'
import PageTitle from '../components/ui/PageTitle'
import { useLeaderboard } from '../hooks/useLeaderboard'
import { formatCreditsPrecise } from '../utils/formatCredits'
import { formatDateTime } from '../utils/formatDateTime'

export default function LeaderboardPage() {
  const { rows, computedAt, stale, rankedUsers, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = useLeaderboard()

  // "No ranking yet" (ledger-service.md): nothing is ranked at all. A
  // ranking that exists but the caller is not on it still shows the board —
  // that case is #39's (the caller's own-row card), not this one.
  const noRankingYet = computedAt === null

  return (
    <AppLayout width="max-w-[900px]">
      <PageTitle className="mb-2">Leaderboard</PageTitle>

      {isLoading && <p className="text-sm text-muted">Loading leaderboard…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load the leaderboard. Please try again.</p>}

      {!isLoading && !hasError && noRankingYet && (
        <p className="text-sm text-muted">Not enough activity yet to rank anyone.</p>
      )}

      {!isLoading && !hasError && !noRankingYet && (
        <>
          <p className="mb-6 text-xs text-subtle">
            Updated {formatDateTime(computedAt)} · {rankedUsers} ranked
            {stale && ' · may be out of date'}
          </p>

          <TableFrame>
            <thead>
              <TableHeaderRow>
                <HeaderCell align="right">Rank</HeaderCell>
                <HeaderCell>Username</HeaderCell>
                <HeaderCell align="right">Net worth</HeaderCell>
              </TableHeaderRow>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr
                  key={row.rank}
                  // The data attribute is a hook for tests; the highlight
                  // itself is colour, which a screen reader does not read, so
                  // the visually-hidden "(you)" is what actually marks the row.
                  data-is-me={row.is_me ? true : undefined}
                  className={`border-b border-line last:border-0 ${row.is_me ? 'bg-smu-gold/10' : ''}`}
                >
                  <NumberCell>{row.rank}</NumberCell>
                  <td className="px-5 py-3 text-smu-navy">
                    {row.username}
                    {row.is_me && <span className="sr-only"> (you)</span>}
                  </td>
                  <NumberCell>{formatCreditsPrecise(row.net_worth)}</NumberCell>
                </tr>
              ))}
            </tbody>
          </TableFrame>

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="rankings" onLoadMore={loadMore} />
          )}
        </>
      )}
    </AppLayout>
  )
}
