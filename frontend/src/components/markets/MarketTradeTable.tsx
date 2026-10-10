import type { MarketTrade } from '../../api/ledgerApi'
import { HeaderCell, NumberCell } from '../ui/Table'
import { formatCreditsPrecise } from '../../utils/formatCredits'

interface MarketTradeTableProps {
  trades: MarketTrade[]
  outcomeLabels: Map<string, string>
}

/** Every trade on one market, across every trader — #8's "trade table: user,
 * side, size, price, timestamp", for spotting manipulation. Unlike
 * LedgerEntryTable (one trader's own history, across many markets), this is
 * one market, across every trader, so the row names its own user instead of
 * a running balance. */
export default function MarketTradeTable({ trades, outcomeLabels }: MarketTradeTableProps) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
      <table className="w-full text-left text-sm">
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
            <tr key={trade.id} className="border-b border-line last:border-0">
              <td className="px-5 py-3 text-muted">{new Date(trade.created_at).toLocaleString('en-SG')}</td>
              <td className="px-5 py-3 text-smu-navy">{trade.username}</td>
              <td className="px-5 py-3 text-smu-navy">{outcomeLabels.get(trade.outcome_id) ?? trade.outcome_id}</td>
              <NumberCell>{trade.side === 'buy' ? 'Buy' : 'Sell'}</NumberCell>
              <NumberCell>{trade.quantity}</NumberCell>
              <NumberCell>{formatCreditsPrecise(trade.average_price)}</NumberCell>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
