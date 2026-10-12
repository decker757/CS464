import { Link } from 'react-router-dom'
import { HeaderCell, NumberCell } from '../ui/Table'
import { formatCreditsPrecise, signedAmountColor } from '../../utils/formatCredits'
import { kindLabel, type EntryRow } from '../../utils/ledgerEntries'

function EntryDescription({ row }: { row: EntryRow }) {
  if (!row.isTrade) {
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

/** One page's worth of ledger rows: date, activity, quantity, average price,
 * signed amount and the running balance. Used by a trader's own history and
 * an administrator's read of someone else's. */
export default function LedgerEntryTable({ rows }: { rows: EntryRow[] }) {
  return (
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
                <span className={signedAmountColor(row.amount)}>{formatCreditsPrecise(row.amount, { showSign: true })}</span>
              </NumberCell>
              <NumberCell>{formatCreditsPrecise(row.balance_after)}</NumberCell>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
