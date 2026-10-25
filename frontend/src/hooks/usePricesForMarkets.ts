import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getMarketSnapshot, getMarketSnapshots, type PriceState } from '../api/ledgerApi'
import type { PublicMarketSummary } from '../api/marketApi'
import { tradingStopped } from '../components/markets/marketStatus'
import { usePriceSocket, type ConnectionStatus } from './usePriceSocket'

/** All the hook needs of a market: which one, and whether it can still trade. */
type PricedMarket = Pick<PublicMarketSummary, 'id' | 'status' | 'close_time'>

export interface MarketPrices {
  /**
   * The whole snapshot, by market id. A market is absent until its own
   * snapshot arrives.
   *
   * The whole answer and not just `prices`: `state_version` is the only thing
   * that tells an untraded book from a contested one, since both quote the
   * same seed price, and `occurred_at` is when the book last moved (#445).
   * Keeping them costs nothing — they are on the wire either way.
   */
  snapshotsByMarket: Map<string, PriceState>
  /**
   * The markets whose last snapshot read failed, from the moment it fails
   * until a later read of the same market answers.
   *
   * A market absent from `snapshotsByMarket` used to mean two things at once
   * — its read is still out, or its read failed — and a card showed the same
   * dash for both, so a trader could not tell whether to wait or to reload
   * ([X-1] #431). This is the difference.
   */
  failedMarkets: Set<string>
  /** When the most recent snapshot landed; null until the first one does. */
  readAt: Date | null
  /** Reads one market's snapshot again, for a card's Retry control (#431). */
  retryMarket: (marketId: string) => void
  /** The price socket's connection state, for the list to say so ([X-4] #37). */
  status: ConnectionStatus
}

// How often the prices on screen are read again. The socket carries every
// trade on a market this page is subscribed to, so this is no longer how a
// price arrives — it is the fallback for markets past the subscription ceiling
// ([X-1] #444) and the backstop for anything the socket missed.
export const PRICE_REFRESH_MS = 30_000

// How many markets this page asks the socket to follow. The server's own
// ceiling is `MAX_SUBSCRIPTIONS_PER_CONNECTION` (50 by default) and it is the
// authority — a refusal is handled whatever this says, because the two numbers
// live in different services and can drift. This only keeps the page from
// sending hundreds of subscribes it knows will be refused.
const MAX_FOLLOWED_MARKETS = 50

// `ids` without `id`. The set is state, so it is replaced rather than edited:
// React compares it by identity.
function withoutId(ids: Set<string>, id: string): Set<string> {
  const remaining = new Set(ids)
  remaining.delete(id)
  return remaining
}

// The current price snapshot of every market in a list, by market id, for the
// browse page's cards ([X-1] #126).
//
// Prices arrive two ways. Every market the page renders is subscribed on one
// socket, and the ledger publishes a frame after each trade commits (ADR
// 0010), so a traded price repaints at once ([X-1] #444). First paint, a
// reconnect and anything past the subscription ceiling come from the ledger's
// batch snapshot instead — one request for the whole page rather than one per
// card ([X-1] #432).
//
// The ceiling is the reason both paths stay. One connection may watch
// `MAX_SUBSCRIPTIONS_PER_CONNECTION` markets, 50 by default, which is exactly
// one page of this list — but the ceiling and the page size are configured in
// different services and "Load more" appends, so a refused subscribe is a
// normal event here, not an edge case. A market the socket will not carry
// falls back to the timer, and nothing on screen is ever silently frozen.
export function usePricesForMarkets(markets: PricedMarket[]): MarketPrices {
  const [failedMarkets, setFailedMarkets] = useState<Set<string>>(new Set())
  const [readAt, setReadAt] = useState<Date | null>(null)
  // Markets this connection was refused a subscription for. They are read on
  // the timer instead, so the page degrades past the ceiling rather than
  // showing a frozen price that looks as live as the rest.
  const [refusedMarkets, setRefusedMarkets] = useState<Set<string>>(new Set())

  // The ids to follow. When the page holds more cards than the socket may
  // watch, the markets a trader just asked for by pressing "Load more" are the
  // ones worth the slots, so the tail of the list wins the subscriptions and
  // the rest fall back to the timer.
  //
  // Kept in the list's own order rather than reversed: the set is compared as
  // an ordered join, so reversing it would churn — every "Load more" would
  // look like a different set and re-subscribe markets already held.
  const followed = useMemo(() => {
    const ids = markets.map(market => market.id)
    return ids.slice(Math.max(0, ids.length - MAX_FOLLOWED_MARKETS))
  }, [markets])

  // A market absent from the batch has no book on the ledger yet: the batch
  // route deliberately opens none, and the single snapshot is what does
  // (ledger-service.md). Asked once per market per page, so a market that is
  // genuinely unknown is not asked about on every tick.
  // The markets a read is currently out for, so a tick cannot queue a second.
  //
  // Cleared when the hook unmounts: StrictMode mounts, unmounts and remounts,
  // and a request that was still out when that happened has its answer applied
  // to state the remount threw away. Left set, it would block the remount's own
  // read and the card would wait for a price for good (#104 amendment D).
  const inFlight = useRef(new Set<string>())
  useEffect(() => () => inFlight.current.clear(), [])
  const openedBooks = useRef(new Set<string>())
  const openBookRef = useRef((marketId: string, apply: (snapshot: PriceState) => void) => {
    if (openedBooks.current.has(marketId)) return
    openedBooks.current.add(marketId)
    getMarketSnapshot(marketId).then(apply, () => {
      setFailedMarkets(previous => new Set(previous).add(marketId))
    })
  })

  // The batch read, for the markets the socket hook asks about: first paint,
  // and every reconnect's ack. One request for the page, not one per card.
  const readSnapshots = useCallback((apply: (snapshot: PriceState) => void, ids: string[]) => {
    if (ids.length === 0) return
    // A tick that fired while the ledger was still answering the last one
    // would queue a second read of the same markets, and a slow ledger is
    // exactly when that happens. Same reason LoadMoreControl disables its
    // button while a page is on its way.
    const wanted = ids.filter(id => !inFlight.current.has(id))
    if (wanted.length === 0) return
    for (const id of wanted) inFlight.current.add(id)
    const settle = () => {
      for (const id of wanted) inFlight.current.delete(id)
    }

    getMarketSnapshots(wanted).then(
      snapshots => {
        settle()
        for (const snapshot of snapshots) apply(snapshot)
        setReadAt(new Date())
        // Only the markets this answer actually carried are cleared: the batch
        // opens no book, so a cold market is absent rather than refused, and
        // the single-snapshot fallback below is what prices it.
        const answered = new Set(snapshots.map(snapshot => snapshot.market_id))
        setFailedMarkets(previous => {
          const remaining = new Set([...previous].filter(id => !answered.has(id)))
          return remaining.size === previous.size ? previous : remaining
        })
        const missing = wanted.filter(id => !answered.has(id))
        for (const id of missing) openBookRef.current(id, apply)
      },
      () => {
        settle()
        // A failure is an answer too: a card keeps the last price it had, or
        // says the price could not be read if it never had one, rather than
        // being asked again before the next refresh.
        setFailedMarkets(previous => {
          const next = new Set(previous)
          for (const id of wanted) next.add(id)
          return next
        })
      },
    )
  }, [])

  const { snapshotsByMarket, status, applySnapshot } = usePriceSocket(followed, {
    onMount: readSnapshots,
    onAck: readSnapshots,
    onRefused: refused => {
      setRefusedMarkets(previous => {
        const next = new Set(previous)
        for (const id of refused) next.add(id)
        return next
      })
    },
    // A refusal belongs to the connection that gave it. The ceiling counts one
    // connection's own subscriptions, so a reconnect starts with room — and a
    // market still named here would be polled for the life of the page while
    // the new connection pushed its frames.
    onConnectionReset: () => setRefusedMarkets(previous => (previous.size === 0 ? previous : new Set())),
  })

  // What the socket is carrying: followed, and not refused a subscription.
  const liveMarkets = useMemo(
    () => new Set(followed.filter(id => !refusedMarkets.has(id))),
    [followed, refusedMarkets],
  )

  // The markets the socket is *not* carrying, re-read on the timer: the ones
  // past the subscription ceiling, and nothing else. A market the relay is
  // pushing needs no poll — that is the whole point of #444 — and polling it
  // anyway would cost the ledger an LMSR evaluation per card twice a minute
  // for a price it has already sent.
  //
  // The clock decides whether a market still trades, not its status column
  // (ADR 0011), so this asks the same question the cards ask: a market that
  // has stopped cannot get a new price, live or polled.
  const pollableIds = markets
    .filter(market => !tradingStopped(market.status, market.close_time))
    .map(market => market.id)
    .filter(id => !liveMarkets.has(id))
    .join(',')
  useEffect(() => {
    if (!pollableIds) return
    const refresh = setInterval(() => {
      readSnapshots(applySnapshot, pollableIds.split(','))
    }, PRICE_REFRESH_MS)
    return () => clearInterval(refresh)
  }, [pollableIds, readSnapshots, applySnapshot])

  // One identity for the life of the hook: it is part of what the hook
  // returns, and a new function on every render would re-run any effect a
  // caller keyed on it.
  const retryMarket = useCallback((marketId: string) => {
    // Back to "still on its way" while this read is out: if it fails too the
    // failure goes back on, and if it lands the success path clears it.
    setFailedMarkets(previous => withoutId(previous, marketId))
    openedBooks.current.delete(marketId)
    getMarketSnapshot(marketId).then(
      snapshot => {
        applySnapshot(snapshot)
        setReadAt(new Date())
        setFailedMarkets(previous => withoutId(previous, marketId))
      },
      () => setFailedMarkets(previous => new Set(previous).add(marketId)),
    )
  }, [applySnapshot])

  return { snapshotsByMarket, failedMarkets, readAt, retryMarket, status }
}
