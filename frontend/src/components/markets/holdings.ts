import type { Position } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { formatCreditsPrecise } from '../../utils/formatCredits'

/** One outcome the trader holds, named and sized, ready to render. */
export interface HeldOutcome {
  outcomeId: string
  label: string
  quantity: string
}

/**
 * What the trader holds in one market, as the card shows it: one entry per
 * outcome, in the market's own outcome order.
 *
 * The ledger's rows carry ids and no labels, and the browse card already has
 * its outcomes — so the join is a lookup here rather than the per-market
 * fetch `utils/marketLabels` does for the portfolio table.
 *
 * **A settled market's rows are left out.** The ledger keeps a position after
 * settlement so it can report what the row was paid (`result` and `payout`,
 * ledger-service.md), but those shares no longer exist: the market paid them
 * out. "You hold 40 Yes" on a settled market would be the card's own lie, and
 * the payout is the portfolio's story to tell, not the browse list's.
 *
 * An outcome the market no longer lists is left out too, rather than rendered
 * as "Unknown": a card has no room to explain one, and the position is an
 * enhancement here.
 */
export function heldOutcomesOf(positions: Position[] | undefined, outcomes: PublicOutcome[]): HeldOutcome[] {
  if (!positions || positions.length === 0) return []

  const held: HeldOutcome[] = []
  for (const outcome of outcomes) {
    const position = positions.find(candidate => candidate.outcome_id === outcome.id)
    if (!position || position.result !== null) continue
    held.push({ outcomeId: outcome.id, label: outcome.label, quantity: formatCreditsPrecise(position.quantity) })
  }
  return held
}
