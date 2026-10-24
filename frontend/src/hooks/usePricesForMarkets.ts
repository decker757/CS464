import { useCallback, useEffect, useRef, useState } from 'react'
import { getMarketSnapshot, type PriceState } from '../api/ledgerApi'
import type { PublicMarketSummary } from '../api/marketApi'
import { tradingStopped } from '../components/markets/marketStatus'

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
   * ([X-1] #431). This is the difference. A market that can still trade is
   * read again on the refresh below, so a failure there clears itself within
   * a tick; one that has stopped trading is never read again by itself, and
   * `retryMarket` is the only way back.
   */
  failedMarkets: Set<string>
  /** When the most recent snapshot landed; null until the first one does. */
  readAt: Date | null
  /** Reads one market's snapshot again, for a card's Retry control (#431). */
  retryMarket: (marketId: string) => void
}

// How often the prices on screen are read again. A browse list left open was
// showing prices from whenever the page loaded, with nothing saying so
// ([X-1] #364). The detail page subscribes to the socket instead; a list of
// cards would have to carry its own copy of that reconnect machinery, so this
// re-reads the same snapshot route it already uses.
export const PRICE_REFRESH_MS = 30_000

// `ids` without `id`. The set is state, so it is replaced rather than edited:
// React compares it by identity.
function withoutId(ids: Set<string>, id: string): Set<string> {
  const remaining = new Set(ids)
  remaining.delete(id)
  return remaining
}

// The current price snapshot of every market in a list, by market id, for the
// browse page's cards ([X-1] #126). A market is missing from the map until its
// snapshot arrives, and stays missing if the request fails — which of the two
// it is comes from `failedMarkets`, so the card can say so and offer to read
// it again (#431).
//
// One snapshot request per market, all at once: #126's Option A. Each lands on
// its own, so a market whose book the ledger is opening for the first time
// holds up only its own card. A batch route on the ledger (Option B) would
// replace the body of this hook and nothing else.
//
// Only markets not asked about yet are requested, so "Load more" prices the
// cards it adds and leaves the ones already shown alone (#104). After that
// every market that can still trade is read again on a timer: a market that
// has stopped trading cannot get a new price, so re-reading it would only cost
// the ledger an LMSR evaluation per card per tick.
export function usePricesForMarkets(markets: PricedMarket[]): MarketPrices {
  const [snapshotsByMarket, setSnapshotsByMarket] = useState<Map<string, PriceState>>(new Map())
  const [failedMarkets, setFailedMarkets] = useState<Set<string>>(new Set())
  const [readAt, setReadAt] = useState<Date | null>(null)
  // A market counts as answered when its request settles, not when it is sent:
  // a request the cleanup cancelled must be sent again, and StrictMode cancels
  // the first mount's requests on purpose.
  // Assumes the market list only grows (Load more appends). If a filter or
  // search replaces it, a card that comes back keeps its first price (or "—"
  // if that fetch failed). The set is private: remount the hook, for example
  // with a `key` on the list, which also clears the prices.
  const answeredIds = useRef(new Set<string>())
  // The current effect run's `read`, for `retryMarket` to call. It closes over
  // that run's in-flight set and its cancellation, which a retry has to
  // respect exactly as the refresh does.
  const readAgain = useRef<(marketId: string) => void>(() => {})

  useEffect(() => {
    // Set by the cleanup, so a list the page has replaced cannot write into it.
    let cancelled = false
    // The markets this list is waiting on. A tick that fired while the ledger
    // was still answering the last one would queue a second read per card, and
    // a slow ledger is exactly when that happens: twenty-four cards against a
    // ledger taking forty-five seconds would have three rounds outstanding at
    // once, each one an LMSR evaluation. The browser's six connections per
    // host then keep the queue full for as long as the ledger is slow.
    // LoadMoreControl disables its button for the same reason.
    // Per effect run, not a ref: a list the cleanup cancelled is asked about
    // again by the next one, which is what StrictMode's double mount needs.
    const inFlightIds = new Set<string>()

    const read = (marketId: string) => {
      if (inFlightIds.has(marketId)) return
      inFlightIds.add(marketId)

      getMarketSnapshot(marketId).then(
        snapshot => {
          inFlightIds.delete(marketId)
          if (cancelled) return
          answeredIds.current.add(marketId)
          setSnapshotsByMarket(previous => new Map(previous).set(marketId, snapshot))
          // A read that answered clears an earlier failure, so a card stops
          // offering to retry something that has just arrived.
          setFailedMarkets(previous => (previous.has(marketId) ? withoutId(previous, marketId) : previous))
          // Only a read that answered moves this. A failing refresh has to show
          // as a time going stale, which is the whole reason the screen says it.
          setReadAt(new Date())
        },
        // A failure is an answer too: the card keeps the last price it had, or
        // says the price could not be read if it never had one, rather than
        // being asked about again before the next refresh.
        () => {
          inFlightIds.delete(marketId)
          if (cancelled) return
          answeredIds.current.add(marketId)
          setFailedMarkets(previous => new Set(previous).add(marketId))
        },
      )
    }

    readAgain.current = read

    for (const market of markets) {
      if (!answeredIds.current.has(market.id)) read(market.id)
    }

    // The clock decides whether a market still trades, not its status column
    // (ADR 0011), so this asks the same question the cards ask.
    const refresh = setInterval(() => {
      for (const market of markets) {
        if (!tradingStopped(market.status, market.close_time)) read(market.id)
      }
    }, PRICE_REFRESH_MS)

    return () => {
      cancelled = true
      clearInterval(refresh)
    }
  }, [markets])

  // One identity for the life of the hook: it is part of what the hook
  // returns, and a new function on every render would re-run any effect a
  // caller keyed on it.
  const retryMarket = useCallback((marketId: string) => {
    // Back to "still on its way" while this read is out: if it fails too the
    // failure goes back on, and if it lands the success path clears it.
    setFailedMarkets(previous => withoutId(previous, marketId))
    readAgain.current(marketId)
  }, [])

  return { snapshotsByMarket, failedMarkets, readAt, retryMarket }
}
