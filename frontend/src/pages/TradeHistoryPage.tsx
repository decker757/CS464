import { Link } from 'react-router-dom'
import { getMyEntries, type LedgerEntry } from '../api/ledgerApi'
import type { PublicMarketDetail } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import LoadMoreControl from '../components/ui/LoadMoreControl'
import PageTitle from '../components/ui/PageTitle'
import { HeaderCell, NumberCell } from '../components/ui/Table'
import { usePagedList } from '../hooks/usePagedList'
import { formatCreditsPrecise, isZeroCredits } from '../utils/formatCredits'
import { labelsFor, loadMarketsById } from '../utils/marketLabels'

interface EntryRow extends LedgerEntry {
  // Only a trade row names a market and an outcome (ledger-service.md); the
  // grant, and any kind this app does not recognise, do not.
  isTrade: boolean
  question: string
  outcomeLabel: string
}

interface EntryPage {
  entries: EntryRow[]
  next_cursor: string | null
}

function toRow(entry: LedgerEntry, markets: Map<string, PublicMarketDetail>): EntryRow {
  const isTrade = entry.kind === 'trade_buy' || entry.kind === 'trade_sell'
  if (!isTrade || !entry.market_id || !entry.outcome_id) {
    return { ...entry, isTrade: false, question: '', outcomeLabel: '' }
  }
  return { ...entry, isTrade: true, ...labelsFor(entry.market_id, entry.outcome_id, markets) }
}

// Module-level, so it keeps its identity and the list is fetched once. Joins
// each page's market questions and outcome labels before handing the page to
// usePagedList, which only knows how to walk pages, not how to build a row.
async function fetchHistoryPage(cursor: string | undefined): Promise<EntryPage> {
  const page = await getMyEntries({ cursor })
  const marketIds = page.entries.flatMap(entry => entry.market_id ? [entry.market_id] : [])
  const markets = await loadMarketsById(marketIds)
  return { entries: page.entries.map(entry => toRow(entry, markets)), next_cursor: page.next_cursor }
}

const pagedHistoryOptions = {
  itemsOf: (page: EntryPage) => page.entries,
  nextCursorOf: (page: EntryPage) => page.next_cursor,
  idOf: (row: EntryRow) => row.id,
}

// An amount of exactly zero cannot happen on a real row today, but the
// formatter stays safe rather than colouring a hypothetical zero as a gain.
function amountColor(amount: string): string {
  if (isZeroCredits(amount)) return 'text-muted'
  if (amount.startsWith('-')) return 'text-danger'
  return 'text-success'
}

// What moved: a human label for a recognised kind, and the kind itself,
// verbatim, for anything this app does not recognise — ledger-service.md
// says to treat an unrecognised kind as opaque, not an error.
function kindLabel(kind: string): string {
  if (kind === 'signup_grant') return 'Starting grant'
  if (kind === 'trade_buy') return 'Buy'
  if (kind === 'trade_sell') return 'Sell'
  return kind
}

function EntryDescription({ row }: { row: EntryRow }) {
  if (!row.isTrade || !row.market_id) {
    return <span className="text-smu-navy">{kindLabel(row.kind)}</span>
  }
  return (
    <div>
      <Link to={`/markets/${row.market_id}`} className="font-medium text-smu-navy hover:underline">
        {row.question}
      </Link>
      <p className="text-xs text-muted">{`${kindLabel(row.kind)} ${row.outcomeLabel}`}</p>
    </div>
  )
}

export default function TradeHistoryPage() {
  const { items: rows, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchHistoryPage, pagedHistoryOptions)

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
          <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs font-semibold tracking-[0.5px] text-subtle uppercase">
                  <HeaderCell>Date</HeaderCell>
                  <HeaderCell>Activity</HeaderCell>
                  <HeaderCell align="right">Quantity</HeaderCell>
                  <HeaderCell align="right">Avg. price</HeaderCell>
                  <HeaderCell align="right">Amount</HeaderCell>
                  <HeaderCell align="right">Balance after</HeaderCell>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.id} className="border-b border-line last:border-0">
                    <td className="px-5 py-3 text-muted">{new Date(row.created_at).toLocaleString('en-SG')}</td>
                    <td className="px-5 py-3"><EntryDescription row={row} /></td>
                    <NumberCell>{row.quantity ?? '—'}</NumberCell>
                    {/* Credits per share, not a probability: it can reach 1 or pass it on a skewed book (ledger-service.md). */}
                    <NumberCell>{row.average_price ? formatCreditsPrecise(row.average_price) : '—'}</NumberCell>
                    <NumberCell>
                      <span className={amountColor(row.amount)}>{formatCreditsPrecise(row.amount)}</span>
                    </NumberCell>
                    <NumberCell>{formatCreditsPrecise(row.balance_after)}</NumberCell>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="history" onLoadMore={loadMore} />
          )}
        </>
      )}
    </AppLayout>
  )
}
