import type { Position, PriceState } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import type { MarketFigures } from '../../hooks/usePricesForMarkets'
import MarketCardGrid from './MarketCardGrid'

interface MarketBandProps {
  /** Stable id, used for the heading the band is labelled by. */
  id: string
  title: string
  /** The rule, stated under the heading, so "why is this here" is never a
   *  mystery ([X-1] #448 AC 2). */
  rule?: string
  markets: PublicMarketSummary[]
  snapshotsByMarket: Map<string, PriceState>
  marketFigures: Map<string, MarketFigures>
  failedMarkets: Set<string>
  positionsByMarket: Map<string, Position[]>
  onRetryPrice: (marketId: string) => void
}

// One named band of cards on the browse page: the featured market, each
// curated section, and the full list below them ([X-1] #448).
//
// A labelled region rather than a bare grid, so each band is addressable on
// its own — which is what lets a reader skip one, and a test scope to it.
export default function MarketBand({
  id,
  title,
  rule,
  markets,
  snapshotsByMarket,
  marketFigures,
  failedMarkets,
  positionsByMarket,
  onRetryPrice,
}: MarketBandProps) {
  return (
    <section aria-labelledby={`section-${id}`} className="mb-8">
      <div className="mb-3">
        <h2 id={`section-${id}`} className="text-base font-bold text-smu-navy">{title}</h2>
        {rule && <p className="text-xs text-subtle">{rule}</p>}
      </div>
      <MarketCardGrid
        markets={markets}
        snapshotsByMarket={snapshotsByMarket}
        marketFigures={marketFigures}
        failedMarkets={failedMarkets}
        positionsByMarket={positionsByMarket}
        onRetryPrice={onRetryPrice}
      />
    </section>
  )
}
