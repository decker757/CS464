import { useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { PricePoint } from '../../api/ledgerApi'
import FilterPill from '../ui/FilterPill'
import { formatPrice } from '../../utils/formatPrice'
import { originalPriceKey, toChartRows, type ChartRow } from '../../utils/priceHistoryChart'

interface PriceHistoryChartProps {
  outcomes: { id: string; label: string }[]
  points: PricePoint[]
}

interface SeriesStyle {
  stroke: string
  /** undefined draws a solid line; recharts wants the attribute absent. */
  strokeDasharray?: string
}

/** The first and last moment a set of points covers, in milliseconds. */
interface TimeBounds {
  oldest: number
  newest: number
}

const HOUR_MS = 60 * 60 * 1000
const DAY_MS = 24 * HOUR_MS

// null is "All", so the pill list and the span it selects are written down in
// one place instead of "All" being a special case at every use (MarketFilters
// does the same with its status pills).
const TIME_RANGES: { label: string; spanMs: number | null }[] = [
  { label: 'Last hour', spanMs: HOUR_MS },
  { label: 'Last day', spanMs: DAY_MS },
  { label: 'Last week', spanMs: 7 * DAY_MS },
  { label: 'All', spanMs: null },
]

// Binary markets only (CLAUDE.md), so two lines is always enough. Outcome
// identity is not gain and loss: success-green and danger-red lend "No" the
// vocabulary of a loss, and they are the one pair a red-green colour-blind
// admin cannot tell apart (#361). The brand pair carries no such meaning, and
// the second line is dashed as well as gold, so the series are still told
// apart in greyscale — the legend below draws each line's own mark.
const SERIES_STYLES: SeriesStyle[] = [
  { stroke: 'var(--color-smu-navy)' },
  { stroke: 'var(--color-smu-gold)', strokeDasharray: '7 4' },
]

// One width for the lines and for the legend marks that stand for them, so a
// swatch is not drawn heavier than the line it names. Recharts' own default
// is 1, which leaves the gold line — the fainter of the two — thinner than it
// needs to be against the grid.
const LINE_WIDTH = 2

function seriesStyle(index: number): SeriesStyle {
  return SERIES_STYLES[index % SERIES_STYLES.length]
}

/** Points arrive oldest first, but toChartRows does not re-sort them, so
 * neither end is read off a position. Every caller holds at least one row;
 * an empty set has no bounds to report. */
function timeBounds(rows: ChartRow[]): TimeBounds {
  let oldest = rows[0].time
  let newest = rows[0].time
  for (const row of rows) {
    oldest = Math.min(oldest, row.time)
    newest = Math.max(newest, row.time)
  }
  return { oldest, newest }
}

/** The rows inside the selected span, measured back from the newest point
 * rather than from now — a market that stopped trading days ago still has a
 * last hour of trading to look at.
 *
 * One point whose `occurred_at` did not parse makes the newest time NaN, and
 * every comparison against NaN is false, so the filter can empty a chart
 * that has points in it. Fall back to the whole history rather than render
 * nothing: a bad timestamp degrades the window, as a bad amount degrades to
 * "—" in formatCredits, and never takes the page down. */
function rowsInRange(rows: ChartRow[], spanMs: number | null): ChartRow[] {
  if (spanMs === null) return rows
  const earliestShown = timeBounds(rows).newest - spanMs
  const inRange = rows.filter(row => row.time >= earliestShown)
  return inRange.length === 0 ? rows : inRange
}

/** Day and month over a long span, time of day within a single day: a market
 * that traded in one afternoon otherwise prints the same date on every tick
 * (#361). A time that did not parse reads "—" rather than "Invalid Date". */
function formatAxisTime(time: number, spanMs: number): string {
  if (!Number.isFinite(time)) return '—'

  const moment = new Date(time)
  if (spanMs <= DAY_MS) {
    return moment.toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit', hour12: false })
  }
  return moment.toLocaleDateString('en-SG', { day: 'numeric', month: 'short' })
}

/** How much of the history the chosen range leaves, so what the control did
 * is readable rather than only visible in the drawing. */
function formatCount(shown: number, total: number): string {
  if (shown < total) return `${shown} of ${total} price points`
  return total === 1 ? 'the only price point' : `all ${total} price points`
}

/** The window the chart is drawing, written the way its ticks are — so the
 * axis format is on the page as text, not only inside the SVG. */
function formatPeriod({ oldest, newest }: TimeBounds, spanMs: number): string {
  const from = formatAxisTime(oldest, spanMs)
  const to = formatAxisTime(newest, spanMs)
  return from === to ? from : `${from} – ${to}`
}

/** One line per outcome, price as a probability percentage over time
 * (#8's "price chart per outcome over time"), over a chosen time range. */
export default function PriceHistoryChart({ outcomes, points }: PriceHistoryChartProps) {
  const [spanMs, setSpanMs] = useState<number | null>(null)

  if (points.length === 0) {
    return <p className="text-sm text-muted">No price history yet.</p>
  }

  const rows = toChartRows(points)
  const shownRows = rowsInRange(rows, spanMs)
  // The span actually drawn, which is what the ticks have to read well over:
  // narrower than the selected range whenever the market traded for less.
  const shownBounds = timeBounds(shownRows)
  const shownSpanMs = shownBounds.newest - shownBounds.oldest

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        {/* Written by hand rather than with recharts' <Legend/>, which would
            do the job on its own — iconType="plainline" draws each line's
            strokeDasharray. The legend and the range control are one strip,
            and the control cannot live inside the chart, so splitting one row
            across two rendering systems costs more than fifteen lines of
            markup. It also puts the outcome names outside a chart that jsdom
            renders at zero width, which is what lets the tests find them. */}
        <ul aria-label="Outcomes" className="flex flex-wrap items-center gap-4">
          {outcomes.map((outcome, index) => (
            <li key={outcome.id} className="flex items-center gap-2 text-sm text-smu-navy">
              <svg aria-hidden="true" width="26" height="8" className="shrink-0">
                <line
                  x1="0"
                  y1="4"
                  x2="26"
                  y2="4"
                  strokeWidth={LINE_WIDTH}
                  stroke={seriesStyle(index).stroke}
                  strokeDasharray={seriesStyle(index).strokeDasharray}
                />
              </svg>
              {outcome.label}
            </li>
          ))}
        </ul>

        <div role="group" aria-label="Time range" className="flex flex-wrap gap-2">
          {TIME_RANGES.map(range => (
            <FilterPill
              key={range.label}
              active={spanMs === range.spanMs}
              aria-pressed={spanMs === range.spanMs}
              onClick={() => setSpanMs(range.spanMs)}
            >
              {range.label}
            </FilterPill>
          ))}
        </div>
      </div>

      <p className="mb-2 text-xs text-subtle">
        Showing {formatCount(shownRows.length, rows.length)}, {formatPeriod(shownBounds, shownSpanMs)}.
      </p>

      <div className="h-[320px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={shownRows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" />
            {/* A time scale, so a burst of trades in one minute takes a
                minute's width rather than one slot per trade. */}
            <XAxis
              dataKey="time"
              type="number"
              scale="time"
              domain={['dataMin', 'dataMax']}
              tickFormatter={time => formatAxisTime(time, shownSpanMs)}
              tick={{ fontSize: 12, fill: 'var(--color-subtle)' }}
            />
            <YAxis
              domain={[0, 100]}
              tickFormatter={value => `${value}%`}
              tick={{ fontSize: 12, fill: 'var(--color-subtle)' }}
            />
            <Tooltip
              labelFormatter={time => (typeof time === 'number' ? new Date(time).toLocaleString('en-SG') : '')}
              formatter={(_value, _name, item) => {
                const row = item.payload as ChartRow | undefined
                const original = row?.[originalPriceKey(String(item.dataKey ?? ''))]
                return typeof original === 'string' ? formatPrice(original) : '—'
              }}
            />
            {outcomes.map((outcome, index) => (
              <Line
                key={outcome.id}
                type="stepAfter"
                dataKey={outcome.id}
                name={outcome.label}
                stroke={seriesStyle(index).stroke}
                strokeDasharray={seriesStyle(index).strokeDasharray}
                strokeWidth={LINE_WIDTH}
                // A single point draws no line, so mark it rather than
                // leaving a range that holds one trade looking empty.
                dot={shownRows.length === 1}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  )
}
