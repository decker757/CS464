import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMyEntries, type LedgerEntry } from '../api/ledgerApi'
import type { PublicMarketDetail } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import Button from '../components/ui/Button'
import PageTitle from '../components/ui/PageTitle'
import { HeaderCell, NumberCell } from '../components/ui/Table'
import { formatCreditsPrecise, isZeroCredits } from '../utils/formatCredits'
import { formatPrice } from '../utils/formatPrice'
import { labelsFor, loadMarketsById } from '../utils/marketLabels'

interface EntryRow extends LedgerEntry {
  question: string
  outcomeLabel: string
}

function toRow(entry: LedgerEntry, markets: Map<string, PublicMarketDetail>): EntryRow {
  // Only a trade row names a market; everything else (the grant, and any
  // kind this app does not recognise) gets the "unknown" fallback, which is
  // never shown since those rows render no market link (ledger-service.md).
  const labels = entry.market_id ? labelsFor(entry.market_id, entry.outcome_id ?? '', markets) : { question: '', outcomeLabel: '' }
  return { ...entry, ...labels }
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
  if (!row.market_id) {
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
  const [rows, setRows] = useState<EntryRow[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loadMoreFailed, setLoadMoreFailed] = useState(false)

  useEffect(() => {
    // StrictMode mounts twice; only the second mount's answer is kept, so a
    // late first answer cannot replace a list Load more has added to.
    let ignored = false
    getMyEntries()
      .then(async page => {
        if (ignored) return
        const marketIds = page.entries.flatMap(e => e.market_id ? [e.market_id] : [])
        const markets = await loadMarketsById(marketIds)
        if (ignored) return
        setRows(page.entries.map(e => toRow(e, markets)))
        setNextCursor(page.next_cursor)
      })
      .catch(() => { if (!ignored) setError(true) })
      .finally(() => { if (!ignored) setLoading(false) })
    return () => { ignored = true }
  }, [])

  function handleLoadMore() {
    if (!nextCursor) return
    setIsLoadingMore(true)
    setLoadMoreFailed(false)
    getMyEntries({ cursor: nextCursor })
      .then(async page => {
        const marketIds = page.entries.flatMap(e => e.market_id ? [e.market_id] : [])
        const markets = await loadMarketsById(marketIds)
        // Appended, never re-sorted: the server's order is the order (ledger-service.md).
        setRows(previous => [...previous, ...page.entries.map(e => toRow(e, markets))])
        setNextCursor(page.next_cursor)
      })
      // The rows already shown stay; the button stays too, so the trader can retry.
      .catch(() => setLoadMoreFailed(true))
      .finally(() => setIsLoadingMore(false))
  }

  return (
    <AppLayout width="max-w-[1100px]">
      <PageTitle className="mb-8">Trade History</PageTitle>

      {loading && <p className="text-sm text-muted">Loading history…</p>}

      {error && <p role="alert" className="text-sm text-danger">Failed to load history. Please try again.</p>}

      {!loading && !error && rows.length === 0 && (
        <p className="text-sm text-muted">No activity yet. Visit the markets page to start trading.</p>
      )}

      {!loading && !error && rows.length > 0 && (
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
                    <td className="px-5 py-3 text-muted">{new Date(row.created_at).toLocaleString()}</td>
                    <td className="px-5 py-3"><EntryDescription row={row} /></td>
                    <NumberCell>{row.quantity ?? '—'}</NumberCell>
                    <NumberCell>{row.average_price ? formatPrice(row.average_price) : '—'}</NumberCell>
                    <NumberCell>
                      <span className={amountColor(row.amount)}>{formatCreditsPrecise(row.amount)}</span>
                    </NumberCell>
                    <NumberCell>{formatCreditsPrecise(row.balance_after)}</NumberCell>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {loadMoreFailed && (
            <p role="alert" className="mt-6 text-sm text-danger">Couldn't load more history. Please try again.</p>
          )}

          {nextCursor && (
            <div className="mt-8 flex justify-center">
              <Button variant="outline" size="md" onClick={handleLoadMore} disabled={isLoadingMore}>
                {isLoadingMore ? 'Loading…' : 'Load more'}
              </Button>
            </div>
          )}
        </>
      )}
    </AppLayout>
  )
}
