import { useEffect, useState } from 'react'
import { getMarket, type PublicMarketDetail } from '../api/marketApi'

/** A market's public detail, loaded by id, plus a label lookup for its
 * outcomes — the fetch/error/outcome-label logic every admin market
 * sub-page (price history, exposure) needs. Not scoped to the market's
 * creator: these pages read oversight data for any market, the same
 * reasoning as DecideOutcomePage. */
export function useMarketDetail(id: string | undefined): { market: PublicMarketDetail | null; hasError: boolean; outcomeLabels: Map<string, string> } {
  const [market, setMarket] = useState<PublicMarketDetail | null>(null)
  const [hasError, setHasError] = useState(false)

  useEffect(() => {
    if (!id) return
    let ignored = false
    getMarket(id)
      .then(found => { if (!ignored) setMarket(found) })
      .catch(() => { if (!ignored) setHasError(true) })
    return () => { ignored = true }
  }, [id])

  const outcomeLabels = new Map((market?.outcomes ?? []).map(outcome => [outcome.id, outcome.label]))

  return { market, hasError, outcomeLabels }
}
