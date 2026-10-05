import { useEffect, useRef, useState } from 'react'
import { getMarketSnapshot, type OutcomePrice } from '../api/ledgerApi'

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
// cards it adds and leaves the ones already shown alone (#104).
export function usePricesForMarkets(marketIds: string[]): Map<string, OutcomePrice[]> {
  const [pricesByMarket, setPricesByMarket] = useState<Map<string, OutcomePrice[]>>(new Map())
  // A market counts as answered when its request settles, not when it is sent:
  // a request the cleanup cancelled must be sent again, and StrictMode cancels
  // the first mount's requests on purpose.
  const answeredIds = useRef(new Set<string>())

  useEffect(() => {
    // Set by the cleanup, so a list the page has replaced cannot write into it.
    let cancelled = false

    for (const marketId of marketIds) {
      if (answeredIds.current.has(marketId)) continue

      getMarketSnapshot(marketId).then(
        snapshot => {
          if (cancelled) return
          answeredIds.current.add(marketId)
          setPricesByMarket(previous => new Map(previous).set(marketId, snapshot.prices))
        },
        // A failure is an answer too: the card shows "—", as it did before
        // anything was appended, rather than being asked about again.
        () => {
          if (cancelled) return
          answeredIds.current.add(marketId)
        },
      )
    }

    return () => { cancelled = true }
  }, [marketIds])

  return pricesByMarket
}
