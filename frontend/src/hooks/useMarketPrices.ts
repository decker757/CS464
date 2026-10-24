import { useCallback, useMemo } from 'react'
import { getMarketSnapshot, type OutcomePrice, type PriceState } from '../api/ledgerApi'
import { usePriceSocket, type ConnectionStatus, type OrderFrame, type PriceSocketOptions } from './usePriceSocket'

export type { OutcomePrice }
export type { ConnectionStatus, OrderFrame }

interface MarketPriceOptions {
  onOrderFrame?: PriceSocketOptions['onOrderFrame']
  /** Called on every `subscribed` ack, the first one included. Frames
   *  published before it never reached this socket — while a dropped
   *  connection was away, or between the caller's mount read and the first
   *  ack — so a listener that tracks state re-reads it here ([X-4] #37's
   *  recovery path, realtime-service.md "Reconnecting"). */
  onSubscribed?: () => void
}

// Current prices and connection status for one market; retains last known
// prices during reconnect rather than blanking them.
//
// The socket, the reconnect, the backoff and the version check live in
// `usePriceSocket`, which follows a set of markets — the browse list follows
// every card it renders ([X-1] #444). This is that hook with a set of one,
// plus this page's own reads of the ledger, which go through the socket hook's
// `applySnapshot` so one high-water mark decides every answer about this book.
export function useMarketPrices(
  marketId: string | null,
  { onOrderFrame, onSubscribed }: MarketPriceOptions = {},
): { prices: OutcomePrice[] | null; status: ConnectionStatus } {
  // A fresh array each render is fine: the socket hook keys on the ids.
  const followed = useMemo(() => (marketId ? [marketId] : []), [marketId])

  // The snapshot comes from the ledger, not the socket, so a realtime service
  // that is down or refuses the origin must not keep a healthy price off the
  // page (AC 1, AC 7). Read for each market newly followed, and again on every
  // ack: that second read covers a trade between the first one and the
  // subscription, and after a drop it covers everything published while we
  // were away. The version check inside the socket hook drops the older answer.
  //
  // The markets to read come from the socket hook as an argument rather than
  // from this closure: it holds the callback in a ref and calls it on an ack,
  // by which time a closure built on an earlier render would still carry that
  // render's id — null, on a detail page whose market has not loaded yet.
  const readSnapshots = useCallback((apply: (snapshot: PriceState) => void, ids: string[]) => {
    for (const id of ids) getMarketSnapshot(id).then(apply, () => {})
  }, [])

  const { snapshotsByMarket, status } = usePriceSocket(followed, {
    onOrderFrame,
    onSubscribed: () => {
      onSubscribed?.()
    },
    onMount: readSnapshots,
    onAck: readSnapshots,
    // One request through `api`, so its interceptor refreshes the session
    // cookie before the socket is retried. The followed market is the one to
    // ask about, and the socket hook ignores the answer if the page has since
    // moved to another.
    onSessionRefresh: followed.length > 0 ? () => getMarketSnapshot(followed[0]) : undefined,
  })

  if (!marketId) return { prices: null, status: 'connecting' }
  return { prices: snapshotsByMarket.get(marketId)?.prices ?? null, status }
}
