import type { OutcomeExposure } from '../../api/ledgerApi'
import { HeaderCell, NumberCell } from '../ui/Table'
import { formatCreditsPrecise } from '../../utils/formatCredits'

interface MarketExposureTableProps {
  outcomes: OutcomeExposure[]
  outcomeLabels: Map<string, string>
}

/** Shares outstanding and the max payout per outcome ([2.2] #6's first two
 * acceptance criteria). Max payout is a hypothetical, not a balance someone
 * holds, but it is still a figure whose decimal places matter, the same
 * category formatCreditsPrecise is for. */
export default function MarketExposureTable({ outcomes, outcomeLabels }: MarketExposureTableProps) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs font-semibold tracking-[0.5px] text-subtle uppercase">
            <HeaderCell>Outcome</HeaderCell>
            <HeaderCell align="right">Shares outstanding</HeaderCell>
            <HeaderCell align="right">Max payout if true</HeaderCell>
          </tr>
        </thead>
        <tbody>
          {outcomes.map(outcome => (
            <tr key={outcome.outcome_id} className="border-b border-line last:border-0">
              <td className="px-5 py-3 text-smu-navy">{outcomeLabels.get(outcome.outcome_id) ?? outcome.outcome_id}</td>
              <NumberCell>{outcome.shares_outstanding}</NumberCell>
              <NumberCell>{formatCreditsPrecise(outcome.max_payout)}</NumberCell>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
