import AppLayout from '../components/layout/AppLayout'
import { HeaderCell, NumberCell, TableFrame } from '../components/ui/Table'
import LoadMoreControl from '../components/ui/LoadMoreControl'
import PageTitle from '../components/ui/PageTitle'
import { useLeaderboard } from '../hooks/useLeaderboard'
import { formatCreditsPrecise } from '../utils/formatCredits'

function formatUpdatedAt(computedAt: string): string {
  return new Date(computedAt).toLocaleString('en-SG', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

export default function LeaderboardPage() {
  const { rows, computedAt, stale, rankedUsers, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = useLeaderboard()

  // "No ranking yet" (ledger-service.md): nothing is ranked at all, as
  // opposed to a ranking that exists but the caller is not on — that case is
  // #39's not-enough-data state, not this page's.
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
            Updated {formatUpdatedAt(computedAt)} · {rankedUsers} ranked
            {stale && ' · may be out of date'}
          </p>

          <TableFrame>
            <thead>
              <tr className="border-b border-line text-xs font-semibold tracking-[0.5px] text-subtle uppercase">
                <HeaderCell align="right">Rank</HeaderCell>
                <HeaderCell>Username</HeaderCell>
                <HeaderCell align="right">Net worth</HeaderCell>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr
                  key={row.rank}
                  data-is-me={row.is_me || undefined}
                  className={`border-b border-line last:border-0 ${row.is_me ? 'bg-smu-gold/10' : ''}`}
                >
                  <NumberCell>{row.rank}</NumberCell>
                  <td className="px-5 py-3 text-smu-navy">{row.username}</td>
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
