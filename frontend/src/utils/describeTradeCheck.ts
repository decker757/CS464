import { formatCreditsPrecise, unsignedCredits } from './formatCredits'
import { nameOfTrade, type SentTrade, type TradeCheck } from './sentTrade'

/** What a trader is told about a trade that got no answer or a 5xx: it may
 *  have gone through, and which trade it was, since they may be looking at
 *  another outcome by now (#580). On an open market, its key is kept in the
 *  browser, so the same trade sent again is never made twice, a reload or
 *  another tab included (#585). A closed market takes no trade to send. */
export function describeUnconfirmedTrade(trade: SentTrade, { canTradeHere = true } = {}): string {
  const opening = `Your ${nameOfTrade(trade)} could not be confirmed, and may have gone through.`
  if (!canTradeHere) return `${opening} Check your portfolio.`
  return `${opening} Check your portfolio before trading again, or send the same trade again: it will not be made twice.`
}

/** What the ledger said about a trade left unconfirmed before this page
 *  loaded, naming it (#585). */
export function describeTradeCheck(check: TradeCheck, { canTradeHere = true } = {}): string {
  const name = nameOfTrade(check.trade)
  if (check.answer === 'went-through') {
    return `Your ${name} went through, for ${formatCreditsPrecise(unsignedCredits(check.total))} credits.`
  }
  if (check.answer === 'did-not-go-through') return `Your ${name} did not go through.`
  if (check.answer === 'checking') return `Checking whether your ${name} went through…`
  return describeUnconfirmedTrade(check.trade, { canTradeHere })
}
