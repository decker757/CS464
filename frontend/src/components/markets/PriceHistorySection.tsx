import { Suspense, lazy, useEffect, useState } from 'react'
import { getMarketPriceHistory, type PricePoint } from '../../api/ledgerApi'
import ErrorBoundary from '../ErrorBoundary'
import SectionCard from '../ui/SectionCard'

interface PriceHistorySectionProps {
  marketId: string
  outcomes: { id: string; label: string }[]
}

type HistoryRead =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'loaded'; points: PricePoint[] }

// recharts roughly doubles the main bundle (App.tsx), and this section sits on
// the busiest route, so the chart stays its own chunk: downloaded only once
// there is a history to draw, and never before the trading panel (#453).
// src/main.test.ts fails if anything reaches it through a static import.
const PriceHistoryChart = lazy(() => import('./PriceHistoryChart'))

// Both while the read is on its way and while the chart's chunk is: to the
// trader they are one wait.
const LOADING = <p className="text-sm text-muted">Loading price history…</p>

const CHART_UNAVAILABLE = (
  <p role="alert" className="text-sm text-danger">Price history unavailable. Reload the page to try again.</p>
)

/** The market's price history for a trader deciding whether to buy, from the
 * route ledger-service.md opens to any role ([FE][T-5] #453). */
export default function PriceHistorySection({ marketId, outcomes }: PriceHistorySectionProps) {
  const [read, setRead] = useState<HistoryRead>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    getMarketPriceHistory(marketId).then(
      history => { if (!cancelled) setRead({ status: 'loaded', points: history.points }) },
      () => { if (!cancelled) setRead({ status: 'failed' }) },
    )
    return () => { cancelled = true }
  }, [marketId])

  return (
    <SectionCard title="Price History" className="mb-6">
      {read.status === 'loading' && LOADING}

      {read.status === 'failed' && <p role="alert" className="text-sm text-danger">Failed to load price history.</p>}

      {/* The first point is the book's opening (ledger-service.md), so one
          point means nobody has traded, and none means the book is not open
          yet. Either way there is no movement to chart, and a lone dot under
          a range control would read as a chart of something. */}
      {read.status === 'loaded' && read.points.length < 2 && (
        <p className="text-sm text-muted">No trades yet. The price has not moved since this market opened.</p>
      )}

      {/* A chunk that fails to download — a deploy replaced it under an open
          tab — or a chart that throws stops here, not at the app's boundary,
          which would take the trading panel down with it. No retry: lazy()
          keeps the rejected import for the session, so only a reload loads
          the chunk again, and the message says so. */}
      {read.status === 'loaded' && read.points.length >= 2 && (
        <ErrorBoundary fallback={CHART_UNAVAILABLE}>
          <Suspense fallback={LOADING}>
            <PriceHistoryChart outcomes={outcomes} points={read.points} />
          </Suspense>
        </ErrorBoundary>
      )}
    </SectionCard>
  )
}
