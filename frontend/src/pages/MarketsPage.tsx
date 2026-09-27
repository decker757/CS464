import { useEffect, useState } from 'react'
import { listMarkets, type PublicMarketSummary } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import MarketCard from '../components/markets/MarketCard'

export default function MarketsPage() {
  const [markets, setMarkets] = useState<PublicMarketSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    listMarkets()
      .then(setMarkets)
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  return (
    <AppLayout width="max-w-[1200px]">
      <h1 className="mb-8 text-[28px] font-extrabold tracking-[-0.3px] text-smu-navy">Markets</h1>

      {loading && <p className="text-sm text-muted">Loading markets…</p>}

      {error && <p role="alert" className="text-sm text-danger">Failed to load markets. Please try again.</p>}

      {!loading && !error && markets.length === 0 && (
        <p className="text-sm text-muted">No markets available right now.</p>
      )}

      {!loading && !error && markets.length > 0 && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(340px,1fr))] gap-4">
          {markets.map((market) => <MarketCard key={market.id} market={market} />)}
        </div>
      )}
    </AppLayout>
  )
}
