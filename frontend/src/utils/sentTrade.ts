import type { TradeSide } from '../api/ledgerApi'
import { compareCredits } from './formatCredits'

/** One trade as the ledger tells trades apart under one idempotency key:
 *  outcome, side and quantity. The label is for the message only. */
export interface SentTrade {
  outcomeId: string
  outcomeLabel: string
  side: TradeSide
  quantity: string
}

/** Whether the ledger would read `a` and `b` as one trade: same outcome and
 *  side, and the quantity compared numerically, so `10` and `10.0` are one
 *  (ledger-service.md's idempotency section). compareCredits reads an
 *  unparseable quantity as equal, which is safe only because a trade is sent
 *  with a preview, and a preview only for a quantity quantityError accepts. */
export function isSameTrade(a: SentTrade, b: SentTrade): boolean {
  return a.outcomeId === b.outcomeId && a.side === b.side && compareCredits(a.quantity, b.quantity) === 0
}
