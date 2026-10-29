import type { OutcomePrice, PriceState } from '../api/ledgerApi'
import { subtractCredits } from './formatCredits'

/**
 * Each outcome's change in price over the last seven days, by outcome id: its
 * price now minus its price then, as a signed decimal string ([X-1] #446).
 *
 * Null when there is nothing to measure from — no snapshot yet, or no
 * `prices_7d_ago`, which the ledger sends for a market with no trade that old
 * (ledger-service.md). Never a zero in its place, and never a change from the
 * seed price: a book standing at its seed is not a price anybody chose.
 *
 * Every outcome, not the first: only on a binary book is the second the first
 * one's mirror. An outcome either side cannot price is left out rather than
 * counted as unmoved.
 */
export function weeklyChangesOf(
  snapshot: PriceState | undefined,
  pricesWeekAgo: OutcomePrice[] | null | undefined,
): Map<string, string> | null {
  if (!snapshot || !pricesWeekAgo) return null

  const changes = new Map<string, string>()
  for (const price of snapshot.prices) {
    const before = pricesWeekAgo.find(old => old.outcome_id === price.outcome_id)
    if (!before) continue
    const change = subtractCredits(price.price, before.price)
    if (change !== null) changes.set(price.outcome_id, change)
  }
  return changes
}
