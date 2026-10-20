import type { OutcomeExposure } from '../../api/ledgerApi'
import { HeaderCell, NumberCell, TableFrame, TableHeaderRow } from '../ui/Table'
import { formatCreditsPrecise, isNegativeCredits, isPlainCredits, signedAmountColor, unsignedCredits } from '../../utils/formatCredits'

interface MarketExposureTableProps {
  outcomes: OutcomeExposure[]
  outcomeLabels: Map<string, string>
}

function formatOrDash(value: string | null): string {
  return value === null ? '—' : formatCreditsPrecise(value)
}

/** A residue in the worst-case line's words (#374), so the column and the
 * line above it say the same thing: a shortfall is what the platform tops
 * up, its minus dropped because "tops up" already says which way it points,
 * and anything else is what the pool keeps. A minus sign alone is the
 * narrowest glyph on the row and colour alone fails a red-green colour-blind
 * admin, so the words carry the direction and the colour backs them (#377). */
function ResidueCell({ residue }: { residue: string | null }) {
  if (residue === null || !isPlainCredits(residue)) return <NumberCell>—</NumberCell>

  const words = isNegativeCredits(residue)
    ? `Platform tops up ${formatCreditsPrecise(unsignedCredits(residue))}`
    : `Pool keeps ${formatCreditsPrecise(residue)}`
  return (
    <NumberCell>
      <span className={signedAmountColor(residue)}>{words}</span>
    </NumberCell>
  )
}

/** Shares outstanding, the payout if an outcome wins, and what the pool would
 * have left over ([2.2] #6's acceptance criteria). Both payout figures are
 * null once the market is settled (docs/api/ledger-service.md). */
export default function MarketExposureTable({ outcomes, outcomeLabels }: MarketExposureTableProps) {
  return (
    <TableFrame>
      <thead>
        <TableHeaderRow>
          <HeaderCell>Outcome</HeaderCell>
          <HeaderCell align="right">Shares outstanding</HeaderCell>
          <HeaderCell align="right">Payout if wins</HeaderCell>
          <HeaderCell align="right">Pool residue if wins</HeaderCell>
        </TableHeaderRow>
      </thead>
      <tbody>
        {outcomes.map(outcome => (
          <tr key={outcome.outcome_id} className="border-b border-line last:border-0">
            <td className="px-5 py-3 text-smu-navy">{outcomeLabels.get(outcome.outcome_id) ?? outcome.outcome_id}</td>
            <NumberCell>{outcome.shares_outstanding}</NumberCell>
            <NumberCell>{formatOrDash(outcome.payout_if_wins)}</NumberCell>
            <ResidueCell residue={outcome.residue_if_wins} />
          </tr>
        ))}
      </tbody>
    </TableFrame>
  )
}
