import { getMarket, type PublicMarketDetail } from '../api/marketApi'

/** A market's question and one of its outcome labels, or the "unknown"
 * fallback when the market failed to load or the outcome no longer exists. */
export interface MarketLabels {
  question: string
  outcomeLabel: string
}

/**
 * Every distinct market named by `marketIds`, keyed by id — one fetch per
 * market, not per row (ledger-service.md: the ledger carries ids only, and
 * labels are the frontend's own join). allSettled, not all: one market
 * failing to load (market_service down, or a 404) must not blank rows whose
 * market loaded fine; callers fall back to "Unknown market" for the gap.
 */
export async function loadMarketsById(marketIds: Iterable<string>): Promise<Map<string, PublicMarketDetail>> {
  const ids = [...new Set(marketIds)]
  const results = await Promise.allSettled(ids.map(getMarket))
  const markets: PublicMarketDetail[] = []
  for (const result of results) {
    if (result.status === 'fulfilled') markets.push(result.value)
  }
  return new Map(markets.map(m => [m.id, m]))
}

/** The question and outcome label for one market/outcome pair, falling back
 * when the market failed to load or no longer has that outcome. */
export function labelsFor(marketId: string, outcomeId: string, markets: Map<string, PublicMarketDetail>): MarketLabels {
  const market = markets.get(marketId)
  const outcome = market?.outcomes.find(o => o.id === outcomeId)
  return {
    question: market?.question ?? 'Unknown market',
    outcomeLabel: outcome?.label ?? 'Unknown outcome',
  }
}
