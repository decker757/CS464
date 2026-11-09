import type { TradeCheck } from '../../utils/sentTrade'
import { describeTradeCheck } from '../../utils/describeTradeCheck'

interface TradeCheckNoticesProps {
  checks: TradeCheck[]
  // Whether this market still takes trades, so "send it again" is advice the
  // trader can follow.
  canTradeHere: boolean
}

function colorOf(check: TradeCheck): string {
  if (check.answer === 'unknown') return 'text-danger'
  if (check.answer === 'went-through') return 'text-success-strong'
  return 'text-muted'
}

/** One line per trade left unconfirmed before this page loaded, saying what
 *  the ledger answered (#585). A check that could not be made is an alert:
 *  the trade may have gone through, and the trader has to act on that. */
export default function TradeCheckNotices({ checks, canTradeHere }: TradeCheckNoticesProps) {
  return checks.map(check => (
    <p key={check.key} role={check.answer === 'unknown' ? 'alert' : 'status'} className={`mb-3 text-xs ${colorOf(check)}`}>
      {describeTradeCheck(check, { canTradeHere })}
    </p>
  ))
}
