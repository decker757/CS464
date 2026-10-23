import { Suspense, lazy, useEffect, useState } from 'react'
import { getMarketPriceHistory, type PricePoint } from '../../api/ledgerApi'
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

/** The market's price history for a trader deciding whether to buy, from the
 * route ledger-service.md opens to any role ([FE][T-5] #453). */
export default function PriceHistorySection({ marketId, outcomes }: PriceHistorySectionProps) {
  const [read, setRead] = useState<HistoryRead>({ status: 'loading' })

  useEffect(() => {
    let ignored = false
    getMarketPriceHistory(marketId).then(
      history => { if (!ignored) setRead({ status: 'loaded', points: history.points }) },
      () => { if (!ignored) setRead({ status: 'failed' }) },
    )
    return () => { ignored = true }
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

      {read.status === 'loaded' && read.points.length >= 2 && (
        <Suspense fallback={LOADING}>
          <PriceHistoryChart outcomes={outcomes} points={read.points} />
        </Suspense>
      )}
    </SectionCard>
  )
}
