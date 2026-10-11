import type { MarketTrade } from '../../api/ledgerApi'
import { HeaderCell, NumberCell, TableFrame } from '../ui/Table'
import { formatCreditsPrecise } from '../../utils/formatCredits'
import { shortId } from '../../utils/shortId'

interface MarketTradeTableProps {
  trades: MarketTrade[]
  outcomeLabels: Map<string, string>
}

/** Every trade on one market, across every trader — #8's "trade table: user,
 * side, size, price, timestamp", for spotting manipulation. Unlike
 * LedgerEntryTable (one trader's own history, across many markets), this is
 * one market, across every trader, so the row names its own user instead of
 * a running balance. The ledger holds no user table, so `user_id` shows as a
 * short id until an admin batch lookup on auth exists (ledger-service.md). */
export default function MarketTradeTable({ trades, outcomeLabels }: MarketTradeTableProps) {
  return (
    <TableFrame>
      <thead>
        <tr className="border-b border-line text-xs font-semibold tracking-[0.5px] text-subtle uppercase">
          <HeaderCell>Date</HeaderCell>
          <HeaderCell>User</HeaderCell>
          <HeaderCell>Outcome</HeaderCell>
          <HeaderCell align="right">Side</HeaderCell>
          <HeaderCell align="right">Quantity</HeaderCell>
          <HeaderCell align="right">Avg. price</HeaderCell>
        </tr>
      </thead>
      <tbody>
        {trades.map(trade => (
          <tr key={trade.transaction_id} className="border-b border-line last:border-0">
            <td className="px-5 py-3 text-muted">{new Date(trade.occurred_at).toLocaleString('en-SG')}</td>
            <td className="px-5 py-3 text-smu-navy" title={trade.user_id}>{shortId(trade.user_id)}</td>
            <td className="px-5 py-3 text-smu-navy">{outcomeLabels.get(trade.outcome_id) ?? trade.outcome_id}</td>
            <NumberCell>{trade.side === 'buy' ? 'Buy' : 'Sell'}</NumberCell>
            <NumberCell>{trade.quantity}</NumberCell>
            <NumberCell>{formatCreditsPrecise(trade.average_price)}</NumberCell>
          </tr>
        ))}
      </tbody>
    </TableFrame>
  )
}
