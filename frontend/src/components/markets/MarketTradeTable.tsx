import { Link } from 'react-router-dom'
import type { MarketTrade } from '../../api/ledgerApi'
import { HeaderCell, NumberCell, TableFrame, TableHeaderRow } from '../ui/Table'
import { formatAveragePrice } from '../../utils/formatCredits'
import { shortId } from '../../utils/shortId'

interface MarketTradeTableProps {
  trades: MarketTrade[]
  outcomeLabels: Map<string, string>
  /** Usernames by user id, from `useUsernames` on the page. An id the lookup
   * has not named — still on its way, unknown to auth, or never answered
   * because the request failed — is absent, and that row shows its short id. */
  usernames: Map<string, string>
}

/** Every trade on one market, across every trader — #8's "trade table: user,
 * side, size, price, timestamp", for spotting manipulation. Unlike
 * LedgerEntryTable (one trader's own history, across many markets), this is
 * one market, across every trader, so the row names its own user instead of
 * a running balance.
 *
 * The ledger sends a `user_id` and no username, because it holds no user
 * table (ADR 0003), so the name is joined in from auth and the cell links to
 * that user's history — the admin screen for spotting manipulation should not
 * make anyone retype a hex fragment into another tab ([2.4] #434). An id the
 * lookup did not name falls back to the short id, and still links: the page
 * it links to is addressed by the id, not by the name. */
export default function MarketTradeTable({ trades, outcomeLabels, usernames }: MarketTradeTableProps) {
  return (
    <TableFrame>
      <thead>
        <TableHeaderRow>
          <HeaderCell>Date</HeaderCell>
          <HeaderCell>User</HeaderCell>
          <HeaderCell>Outcome</HeaderCell>
          <HeaderCell align="right">Side</HeaderCell>
          <HeaderCell align="right">Quantity</HeaderCell>
          <HeaderCell align="right">Avg. price</HeaderCell>
        </TableHeaderRow>
      </thead>
      <tbody>
        {trades.map(trade => (
          <tr key={trade.transaction_id} className="border-b border-line last:border-0">
            <td className="px-5 py-3 text-muted">{new Date(trade.created_at).toLocaleString('en-SG')}</td>
            <td className="px-5 py-3" title={trade.user_id}>
              <Link to={`/admin/users/${trade.user_id}`} className="font-medium text-smu-navy hover:underline">
                {usernames.get(trade.user_id) ?? shortId(trade.user_id)}
              </Link>
            </td>
            <td className="px-5 py-3 text-smu-navy">{outcomeLabels.get(trade.outcome_id) ?? trade.outcome_id}</td>
            <NumberCell>{trade.side === 'buy' ? 'Buy' : 'Sell'}</NumberCell>
            <NumberCell>{trade.quantity}</NumberCell>
            <NumberCell>{formatAveragePrice(trade.average_price)}</NumberCell>
          </tr>
        ))}
      </tbody>
    </TableFrame>
  )
}
