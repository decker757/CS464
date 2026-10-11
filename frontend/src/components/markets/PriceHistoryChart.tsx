import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { PricePoint } from '../../api/ledgerApi'
import { formatPrice } from '../../utils/formatPrice'
import { originalPriceKey, toChartRows, type ChartRow } from '../../utils/priceHistoryChart'

interface PriceHistoryChartProps {
  outcomes: { id: string; label: string }[]
  points: PricePoint[]
}

// Binary markets only (CLAUDE.md), so two lines is always enough — the first
// outcome by position in the success colour, the second in danger, the same
// gain/loss vocabulary signedAmountColor already uses elsewhere.
const LINE_COLORS = ['var(--color-success)', 'var(--color-danger)']

/** One line per outcome, price as a probability percentage over time
 * (#8's "price chart per outcome over time"). */
export default function PriceHistoryChart({ outcomes, points }: PriceHistoryChartProps) {
  if (points.length === 0) {
    return <p className="text-sm text-muted">No price history yet.</p>
  }

  const rows = toChartRows(points)

  return (
    <div className="h-[320px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" />
          <XAxis
            dataKey="occurred_at"
            tickFormatter={value => new Date(value).toLocaleDateString('en-SG', { day: 'numeric', month: 'short' })}
            tick={{ fontSize: 12, fill: 'var(--color-subtle)' }}
          />
          <YAxis
            domain={[0, 100]}
            tickFormatter={value => `${value}%`}
            tick={{ fontSize: 12, fill: 'var(--color-subtle)' }}
          />
          <Tooltip
            labelFormatter={label => (typeof label === 'string' ? new Date(label).toLocaleString('en-SG') : '')}
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
              stroke={LINE_COLORS[index % LINE_COLORS.length]}
              dot={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
