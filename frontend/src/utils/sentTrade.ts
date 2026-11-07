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

/** What the ledger said about a trade left unconfirmed before this page
 *  loaded (#585). `unknown` when it could not be asked: the key is kept. */
export type TradeCheck =
  | { key: string; trade: SentTrade; answer: 'checking' | 'did-not-go-through' | 'unknown' }
  | { key: string; trade: SentTrade; answer: 'went-through'; total: string }

/** A trade as the messages name it: "buy of 10 Yes". */
export function nameOfTrade({ side, quantity, outcomeLabel }: Pick<SentTrade, 'side' | 'quantity' | 'outcomeLabel'>): string {
  return `${side} of ${quantity} ${outcomeLabel}`
}

/** Whether the ledger would read `a` and `b` as one trade: same outcome and
 *  side, and the quantity compared numerically, so `10` and `10.0` are one
 *  (ledger-service.md's idempotency section). compareCredits reads an
 *  unparseable quantity as equal, which is safe only because a trade is sent
 *  with a preview, and a preview only for a quantity quantityError accepts. */
export function isSameTrade(a: SentTrade, b: SentTrade): boolean {
  return a.outcomeId === b.outcomeId && a.side === b.side && compareCredits(a.quantity, b.quantity) === 0
}
