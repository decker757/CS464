import type { Position, PriceState } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import type { MarketFigures } from '../../hooks/usePricesForMarkets'
import MarketCard from './MarketCard'

interface MarketCardGridProps {
  markets: PublicMarketSummary[]
  snapshotsByMarket: Map<string, PriceState>
  /** The batch-only figures, by market id, for each card's change over the week (#564). */
  marketFigures: Map<string, MarketFigures>
  failedMarkets: Set<string>
  positionsByMarket: Map<string, Position[]>
  onRetryPrice: (marketId: string) => void
}

// A responsive grid of market cards, with everything a card needs wired to it
// from the page's own reads.
//
// Its own component because the browse page renders several: the full list,
// and one per curated band above it ([X-1] #448). The bands and the list show
// the same card from the same prices and the same holdings — only the markets
// differ — so the wiring lives here once.
export default function MarketCardGrid({
  markets,
  snapshotsByMarket,
  marketFigures,
  failedMarkets,
  positionsByMarket,
  onRetryPrice,
}: MarketCardGridProps) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4">
      {markets.map(market => (
        <MarketCard
          key={market.id}
          market={market}
          snapshot={snapshotsByMarket.get(market.id)}
          priceReadFailed={failedMarkets.has(market.id)}
          onRetryPrice={() => onRetryPrice(market.id)}
          positions={positionsByMarket.get(market.id)}
          pricesWeekAgo={marketFigures.get(market.id)?.prices_7d_ago}
        />
      ))}
    </div>
  )
}
