import { useEffect, useRef, useState } from 'react'
import { getMarketSnapshot, type OutcomePrice } from '../api/ledgerApi'
import type { PublicMarketSummary } from '../api/marketApi'
import { tradingStopped } from '../components/markets/marketStatus'

/** All the hook needs of a market: which one, and whether it can still trade. */
type PricedMarket = Pick<PublicMarketSummary, 'id' | 'status' | 'close_time'>

export interface MarketPrices {
  /** By market id. A market is absent until its snapshot arrives. */
  pricesByMarket: Map<string, OutcomePrice[]>
  /** When the most recent snapshot landed; null until the first one does. */
  readAt: Date | null
}

// How often the prices on screen are read again. A browse list left open was
// showing prices from whenever the page loaded, with nothing saying so
// ([X-1] #364). The detail page subscribes to the socket instead; a list of
// cards would have to carry its own copy of that reconnect machinery, so this
// re-reads the same snapshot route it already uses.
export const PRICE_REFRESH_MS = 30_000

// The current prices of every market in a list, by market id, for the browse
// page's cards ([X-1] #126). A market is missing from the map until its
// snapshot arrives, and stays missing if the request fails, so its card shows
// "—" either way.
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
  const [pricesByMarket, setPricesByMarket] = useState<Map<string, OutcomePrice[]>>(new Map())
  const [readAt, setReadAt] = useState<Date | null>(null)
  // A market counts as answered when its request settles, not when it is sent:
  // a request the cleanup cancelled must be sent again, and StrictMode cancels
  // the first mount's requests on purpose.
  // Assumes the market list only grows (Load more appends). If a filter or
  // search replaces it, a card that comes back keeps its first price (or "—"
  // if that fetch failed). The set is private: remount the hook, for example
  // with a `key` on the list, which also clears the prices.
  const answeredIds = useRef(new Set<string>())

  useEffect(() => {
    // Set by the cleanup, so a list the page has replaced cannot write into it.
    let cancelled = false

    const read = (marketId: string) => {
      getMarketSnapshot(marketId).then(
        snapshot => {
          if (cancelled) return
          answeredIds.current.add(marketId)
          setPricesByMarket(previous => new Map(previous).set(marketId, snapshot.prices))
          setReadAt(new Date())
        },
        // A failure is an answer too: the card keeps the last price it had, or
        // shows "—" if it never had one, rather than being asked about again
        // before the next refresh.
        () => {
          if (cancelled) return
          answeredIds.current.add(marketId)
        },
      )
    }

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

  return { pricesByMarket, readAt }
}
