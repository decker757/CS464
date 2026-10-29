import type { PricePoint } from '../api/ledgerApi'

// Recharts plots real numbers, not decimal strings — unlike a displayed
// balance or cost, a point's y-coordinate is a screen position, not a figure
// the user reads off directly (formatCredits.ts's parseFloat rule is about
// money arithmetic, not plotting). Every row also carries the original
// decimal string beside the plotted number, so the tooltip — which the
// viewer does read directly — can format it with formatPrice instead of
// re-deriving a label from the lossy intermediate number.
function toPercent(price: string): number | null {
  const parsed = Number(price)
  return Number.isFinite(parsed) ? parsed * 100 : null
}

// originalPriceKey(outcomeId) names the row's other key, carrying the
// decimal string the plotted number at outcomeId was computed from.
export function originalPriceKey(outcomeId: string): string {
  return `${outcomeId}:price`
}

export interface ChartRow {
  // The point's occurred_at in milliseconds, so the x axis can space points
  // by when they happened rather than one slot per trade.
  time: number
  [outcomeIdOrPriceKey: string]: number | string | null
}

/** Points already come oldest first, one per trade, each naming every
 * outcome's price at that moment (ledgerApi.ts) — so this is a row per
 * point, not a group-by-time the way separate per-outcome points would need. */
export function toChartRows(points: PricePoint[]): ChartRow[] {
  return points.map(point => {
    const row: ChartRow = { time: Date.parse(point.occurred_at) }
    for (const price of point.prices) {
      row[price.outcome_id] = toPercent(price.price)
      row[originalPriceKey(price.outcome_id)] = price.price
    }
    return row
  })
}

/**
 * The history with one live frame folded in ([T-5] #495).
 *
 * A point is a `price` socket frame without `type` and `market_id`
 * (ledger-service.md), so a frame appends as a point — and a frame whose
 * `state_version` the chart already holds is dropped, which is what keeps a
 * reconnect or a refetch from drawing the same trade twice.
 *
 * **Deduped on `state_version`, never on time.** A point's `occurred_at` is
 * when the trade's entries landed, and the frame's is when the trade took its
 * price under the book lock, so the same trade carries two different times and
 * across replicas the gap is clock skew (ledger-service.md).
 *
 * A frame older than the newest point is dropped rather than inserted. The
 * ledger publishes in `state_version` order and the socket hook already
 * refuses a frame that is not newer than what it holds, so an older one here
 * means a late arrival the chart has moved past — and splicing it into the
 * middle would redraw a line the trader has already read.
 */
export function withLiveFrame(points: PricePoint[], frame: PricePoint | null): PricePoint[] {
  if (!frame) return points

  const newest = points.at(-1)
  if (newest && frame.state_version <= newest.state_version) return points
  return [...points, frame]
}
