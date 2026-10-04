import { useEffect, useState } from 'react'
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
export function usePricesForMarkets(marketIds: string[]): Map<string, OutcomePrice[]> {
  const [pricesByMarket, setPricesByMarket] = useState<Map<string, OutcomePrice[]>>(new Map())

  useEffect(() => {
    // Set by the cleanup, so a list the page has replaced cannot write into it.
    let cancelled = false

    for (const marketId of marketIds) {
      getMarketSnapshot(marketId).then(
        snapshot => {
          if (cancelled) return
          setPricesByMarket(previous => new Map(previous).set(marketId, snapshot.prices))
        },
        // Nothing to record: a market with no prices shows "—".
        () => {},
      )
    }

    return () => { cancelled = true }
  }, [marketIds])

  return pricesByMarket
}
