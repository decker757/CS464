import { useState } from 'react'
import type { MarketOverviewRow } from '../api/marketApi'

interface JustNow {
  voidedJustNow: boolean
  disputedJustNow: boolean
  settledJustNow: boolean
  closedJustNow: boolean
  publishedJustNow: boolean
}

// A void is final, so it beats everything, a send-back from this row a moment
// earlier included. A send-back returns the market to closed, which beats the
// rest; after that settled wins over closed because a market is closed before
// it is settled, and closed wins over published because a market published
// from this row can be closed early from it a moment later.
function statusAfter(market: MarketOverviewRow, { voidedJustNow, disputedJustNow, settledJustNow, closedJustNow, publishedJustNow }: JustNow) {
  if (voidedJustNow) return 'voided'
  if (disputedJustNow) return 'closed'
  if (settledJustNow) return 'settled'
  if (closedJustNow) return 'closed'
  if (publishedJustNow) return 'open'
  return market.status
}

// The status an admin markets row shows: the list's, until an action on the
// row succeeds, so the row repaints without waiting for the list's next fetch
// (market-service.md's notes for #56, point 5). Each mark is the success
// callback for one action's hook.
export function useStatusAfterActions(market: MarketOverviewRow) {
  const [closedJustNow, setClosedJustNow] = useState(false)
  const [settledJustNow, setSettledJustNow] = useState(false)
  // A send-back, which returns the market to closed.
  const [disputedJustNow, setDisputedJustNow] = useState(false)
  const [publishedJustNow, setPublishedJustNow] = useState(false)
  // Set once a void answers 200.
  const [voidedJustNow, setVoidedJustNow] = useState(false)

  return {
    status: statusAfter(market, { voidedJustNow, disputedJustNow, settledJustNow, closedJustNow, publishedJustNow }),
    markClosed: () => setClosedJustNow(true),
    markSettled: () => setSettledJustNow(true),
    markDisputed: () => setDisputedJustNow(true),
    markPublished: () => setPublishedJustNow(true),
    markVoided: () => setVoidedJustNow(true),
  }
}
