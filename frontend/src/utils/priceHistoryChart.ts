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
  occurred_at: string
  [outcomeIdOrPriceKey: string]: number | string | null
}

/** Points already come oldest first, one per trade, each naming every
 * outcome's price at that moment (ledgerApi.ts) — so this is a row per
 * point, not a group-by-time the way separate per-outcome points would need. */
export function toChartRows(points: PricePoint[]): ChartRow[] {
  return points.map(point => {
    const row: ChartRow = { occurred_at: point.occurred_at }
    for (const price of point.prices) {
      row[price.outcome_id] = toPercent(price.price)
      row[originalPriceKey(price.outcome_id)] = price.price
    }
    return row
  })
}
