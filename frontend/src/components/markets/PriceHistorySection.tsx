import { Suspense, lazy, useEffect, useState } from 'react'
import { getMarketPriceHistory, type PricePoint, type PriceState } from '../../api/ledgerApi'
import { withLiveFrame } from '../../utils/priceHistoryChart'
import ErrorBoundary from '../ErrorBoundary'
import SectionCard from '../ui/SectionCard'

interface PriceHistorySectionProps {
  marketId: string
  outcomes: { id: string; label: string }[]
  /**
   * The book state the market's socket last reported, or null before the
   * first one arrives ([T-5] #495).
   *
   * A point is a `price` frame without `type` and `market_id`
   * (ledger-service.md), so each one this section sees is kept and folded
   * into the history — which is what keeps the chart level with the price
   * cards above it after somebody else's trade.
   */
  liveSnapshot?: PriceState | null
  /**
   * Bumped whenever the market's socket re-subscribes, so the history is read
   * again.
   *
   * Frames published while a dropped connection was away are gone — Redis
   * pub/sub buffers nothing (ADR 0010) — so a reconnect cannot be caught up
   * from frames and has to re-read ([X-4] #37's recovery path, and #495's
   * third criterion).
   */
  reconnects?: number
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
export default function PriceHistorySection({
  marketId,
  outcomes,
  liveSnapshot = null,
  reconnects = 0,
}: PriceHistorySectionProps) {
  const [read, setRead] = useState<HistoryRead>({ status: 'loading' })

  // Every book state the socket has reported since this section mounted,
  // oldest first. The socket hook keeps only the newest per market, so drawing
  // that alone would drop one trade's point as soon as the next one arrived.
  // Kept apart from the read, so a read that lands after a frame — begun
  // before that trade, or re-read after a reconnect — cannot wipe it out.
  const [liveFrames, setLiveFrames] = useState<PricePoint[]>(() => (liveSnapshot ? [liveSnapshot] : []))
  const [lastSnapshot, setLastSnapshot] = useState(liveSnapshot)
  if (liveSnapshot !== lastSnapshot) {
    setLastSnapshot(liveSnapshot)
    setLiveFrames(frames => withLiveFrame(frames, liveSnapshot))
  }

  useEffect(() => {
    let cancelled = false
    getMarketPriceHistory(marketId).then(
      history => { if (!cancelled) setRead({ status: 'loaded', points: history.points }) },
      () => { if (!cancelled) setRead({ status: 'failed' }) },
    )
    return () => { cancelled = true }
    // `reconnects` is not read in here, and is the point: a new value re-runs
    // this effect, which is the re-read a reconnect needs.
  }, [marketId, reconnects])

  // The history the chart draws: what the ledger returned, plus each frame
  // newer than its last point. A frame the history already holds is dropped,
  // so the read after a reconnect and the frames around it cannot draw the
  // same trade twice.
  let points: PricePoint[] = []
  if (read.status === 'loaded') {
    points = read.points
    for (const frame of liveFrames) points = withLiveFrame(points, frame)
  }

  return (
    <SectionCard title="Price History" className="mb-6">
      {read.status === 'loading' && LOADING}

      {read.status === 'failed' && <p role="alert" className="text-sm text-danger">Failed to load price history.</p>}

      {/* The first point is the book's opening (ledger-service.md), so one
          point means nobody has traded, and none means the book is not open
          yet. Either way there is no movement to chart, and a lone dot under
          a range control would read as a chart of something. */}
      {read.status === 'loaded' && points.length < 2 && (
        <p className="text-sm text-muted">No trades yet. The price has not moved since this market opened.</p>
      )}

      {/* A chunk that fails to download — a deploy replaced it under an open
          tab — or a chart that throws stops here, not at the app's boundary,
          which would take the trading panel down with it. No retry: lazy()
          keeps the rejected import for the session, so only a reload loads
          the chunk again, and the message says so. */}
      {read.status === 'loaded' && points.length >= 2 && (
        <ErrorBoundary fallback={CHART_UNAVAILABLE}>
          <Suspense fallback={LOADING}>
            <PriceHistoryChart outcomes={outcomes} points={points} />
          </Suspense>
        </ErrorBoundary>
      )}
    </SectionCard>
  )
}
