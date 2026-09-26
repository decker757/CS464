import { useEffect, useState } from 'react'
import { listMarkets, type PublicMarketSummary } from '../api/marketApi'
import AppNavbar from '../components/AppNavbar'
import MarketCard from '../components/markets/MarketCard'
import { CREAM, NAV } from '../theme/colors'

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
    <div style={{ minHeight: '100vh', backgroundColor: CREAM }}>
      <AppNavbar />
      <main style={{ maxWidth: 1200, margin: '0 auto', padding: '48px 32px' }}>
        <h1 style={{ fontSize: 28, fontWeight: 800, color: NAV, marginBottom: 32, letterSpacing: '-0.3px' }}>
          Markets
        </h1>

        {loading && (
          <p style={{ color: '#6b7280', fontSize: 14 }}>Loading markets…</p>
        )}

        {error && (
          <p role="alert" style={{ color: '#dc2626', fontSize: 14 }}>
            Failed to load markets. Please try again.
          </p>
        )}

        {!loading && !error && markets.length === 0 && (
          <p style={{ color: '#6b7280', fontSize: 14 }}>No markets available right now.</p>
        )}

        {!loading && !error && markets.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
            {markets.map((m) => (
              <MarketCard key={m.id} market={m} />
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
