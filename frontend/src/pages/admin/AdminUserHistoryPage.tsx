import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getUserBalance, getUserEntries, type AdminBalance } from '../../api/ledgerApi'
import AppLayout from '../../components/layout/AppLayout'
import LedgerEntryTable from '../../components/ledger/LedgerEntryTable'
import BackLink from '../../components/ui/BackLink'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import { usePagedList } from '../../hooks/usePagedList'
import { formatCreditsPrecise } from '../../utils/formatCredits'
import { joinEntryPage, pagedEntryOptions } from '../../utils/ledgerEntries'

// [FE][4.1] #57. Reading another user's balance and history never mints a
// grant, unlike the /me routes a trader's own pages use (ledger-service.md):
// a user who has not read their own yet shows a zero balance and an empty
// history here, not an error.
export default function AdminUserHistoryPage() {
  const { userId } = useParams<{ userId: string }>()

  const [balance, setBalance] = useState<AdminBalance | null>(null)
  const [balanceError, setBalanceError] = useState(false)

  useEffect(() => {
    if (!userId) return
    getUserBalance(userId)
      .then(setBalance)
      .catch(() => setBalanceError(true))
  }, [userId])

  const fetchPage = useCallback(
    (cursor: string | undefined) => joinEntryPage(() => getUserEntries(userId ?? '', { cursor })),
    [userId],
  )
  const { items: rows, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchPage, pagedEntryOptions)

  return (
    <AppLayout width="max-w-[1100px]">
      <BackLink to="/admin/users" label="Users" className="mb-4 block" />
      <PageTitle className="mb-2">User History</PageTitle>

      {balanceError && <p role="alert" className="mb-6 text-sm text-danger">Failed to load balance.</p>}
      {balance && (
        <p className="mb-8 text-sm text-muted">
          Current balance: <span className="font-semibold text-smu-navy">{formatCreditsPrecise(balance.balance)} credits</span>
        </p>
      )}

      {isLoading && <p className="text-sm text-muted">Loading history…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load history. Please try again.</p>}

      {!isLoading && !hasError && rows.length === 0 && (
        <p className="text-sm text-muted">No activity for this user yet.</p>
      )}

      {!isLoading && !hasError && rows.length > 0 && (
        <>
          <LedgerEntryTable rows={rows} />

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="history" onLoadMore={loadMore} />
          )}
        </>
      )}
    </AppLayout>
  )
}
