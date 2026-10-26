import { Link } from 'react-router-dom'
import { HeaderCell, NumberCell, TableHeaderRow } from '../ui/Table'
import { formatAveragePrice, formatCreditsPrecise, signedAmountColor } from '../../utils/formatCredits'
import { kindLabel, type EntryRow } from '../../utils/ledgerEntries'

function EntryDescription({ row }: { row: EntryRow }) {
  if (!row.isTrade && !row.isSettlement && !row.isRefund) {
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
 * an administrator's read of someone else's.
 *
 * Each figure keeps the four decimal places the ledger sent, unlike the
 * glanceable figures in the navbar and on the portfolio's cards, which are
 * rounded to the cent (#443). This is the column a reader checks the
 * arithmetic down — `balance_after` minus `amount` is the row below it
 * exactly (ledger-service.md) — and it sits beside an average price that is
 * credits per share and cannot be rounded to the cent (#247).
 *
 * The unit is named once, in the header of each money column, rather than
 * after every figure: "balance after" is the same quantity as the navbar's
 * "credits" and the portfolio's old "cash" card, and nothing on screen said
 * so (#443). Quantity and average price are left as they are, because the
 * market's own trade log has those two columns and not these.
 */
export default function LedgerEntryTable({ rows }: { rows: EntryRow[] }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
      <table className="w-full text-left text-sm">
        <thead>
          <TableHeaderRow>
            <HeaderCell>Date</HeaderCell>
            <HeaderCell>Activity</HeaderCell>
            <HeaderCell align="right">Quantity</HeaderCell>
            <HeaderCell align="right">Avg. price</HeaderCell>
            <HeaderCell align="right">Amount (credits)</HeaderCell>
            <HeaderCell align="right">Balance after (credits)</HeaderCell>
          </TableHeaderRow>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id} className="border-b border-line last:border-0">
              <td className="px-5 py-3 text-muted">{new Date(row.created_at).toLocaleString('en-SG')}</td>
              <td className="px-5 py-3"><EntryDescription row={row} /></td>
              <NumberCell>{row.quantity ?? '—'}</NumberCell>
              <NumberCell>{row.average_price ? formatAveragePrice(row.average_price) : '—'}</NumberCell>
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
