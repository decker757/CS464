import { getMyEntries } from '../api/ledgerApi'
import AppLayout from '../components/layout/AppLayout'
import LedgerEntryTable from '../components/ledger/LedgerEntryTable'
import LoadMoreControl from '../components/ui/LoadMoreControl'
import PageTitle from '../components/ui/PageTitle'
import { usePagedList } from '../hooks/usePagedList'
import { joinEntryPage, pagedEntryOptions } from '../utils/ledgerEntries'

// Module-level, so it keeps its identity and the list is fetched once.
function fetchHistoryPage(cursor: string | undefined) {
  return joinEntryPage(() => getMyEntries({ cursor }))
}

export default function TradeHistoryPage() {
  const { items: rows, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchHistoryPage, pagedEntryOptions)

  return (
    <AppLayout width="max-w-[1100px]">
      <PageTitle className="mb-8">Trade History</PageTitle>

      {isLoading && <p className="text-sm text-muted">Loading history…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load history. Please try again.</p>}

      {!isLoading && !hasError && rows.length === 0 && (
        <p className="text-sm text-muted">No activity yet. Visit the markets page to start trading.</p>
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
