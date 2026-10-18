import { formatPrice } from '../../utils/formatPrice'

// A static, decorative market card for the landing page. None of it is live
// data, and the card says so on its face — a visitor who reads it should not
// come away believing this market or these prices exist.
//
// The made-up prices are decimal strings, the way the API sends a real one, so
// the card shows them through the app's own formatter instead of inventing a
// second way to write a price.
const EXAMPLE_YES_PRICE = '0.64'
const EXAMPLE_YES_PRICE_A_WEEK_AGO = '0.52'

const EXAMPLE_OUTCOMES = [
  { label: 'YES', price: EXAMPLE_YES_PRICE, text: 'text-success', bar: 'bg-success' },
  { label: 'NO', price: '0.36', text: 'text-danger', bar: 'bg-danger' },
]

const SPARKLINE_POINTS: [number, number][] = [
  [0, 52], [12, 50], [24, 48], [36, 51], [48, 55],
  [60, 58], [72, 56], [84, 60], [96, 62], [108, 64],
]

function Sparkline() {
  const width = 108
  const height = 36
  const ys = SPARKLINE_POINTS.map((point) => point[1])
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const scaleY = (y: number) => height - ((y - minY) / (maxY - minY)) * height

  const linePath = SPARKLINE_POINTS
    .map(([x, y], i) => {
      const command = i === 0 ? 'M' : 'L' // move to the first point, draw a line to the rest
      return `${command} ${x} ${scaleY(y)}`
    })
    .join(' ')
  const areaPath = `${linePath} L ${width} ${height} L 0 ${height} Z`
  const [lastX, lastY] = SPARKLINE_POINTS[SPARKLINE_POINTS.length - 1]

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="block overflow-visible text-success">
      <defs>
        <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.18" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#sparkGrad)" />
      <path d={linePath} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lastX} cy={scaleY(lastY)} r="3" fill="currentColor" />
    </svg>
  )
}

export default function MarketPreviewCard() {
  return (
    <div className="w-full rounded-2xl border border-t-[3px] border-smu-gold/30 border-t-smu-gold bg-white px-6 pt-6 pb-5 shadow-hero">
      <div className="mb-3.5 flex items-center justify-between">
        <span className="rounded-full bg-smu-gold/10 px-[9px] py-[3px] text-[10px] font-bold tracking-[0.6px] text-smu-gold uppercase">
          Example
        </span>
        <span className="text-[11px] text-muted">Closes 30 Oct</span>
      </div>

      <p className="mb-4 text-sm leading-[1.45] font-semibold text-smu-navy">
        Will SMU's handball team win SUNIG this year?
      </p>

      <div className="mb-3.5 flex items-end justify-between">
        <div>
          <p className="mb-1 text-[10px] text-subtle">YES · 7-day trend</p>
          <Sparkline />
        </div>
        <div className="text-right">
          <p className="mb-0.5 text-[10px] text-subtle">Current</p>
          <p className="text-[22px] leading-none font-extrabold text-success">{formatPrice(EXAMPLE_YES_PRICE)}</p>
          <p className="text-[11px] text-success">▲ up from {formatPrice(EXAMPLE_YES_PRICE_A_WEEK_AGO)}</p>
        </div>
      </div>

      <div className="mb-4 flex flex-col gap-2">
        {EXAMPLE_OUTCOMES.map(({ label, price, text, bar }) => {
          // A formatted price is a percentage, so it is also the bar's width.
          const percent = formatPrice(price)
          return (
            <div key={label} className="flex items-center gap-2.5 text-xs">
              <span className={`w-7 font-bold ${text}`}>{label}</span>
              <div className="h-[5px] flex-1 overflow-hidden rounded-full bg-track">
                <div className={`h-full rounded-full ${bar}`} style={{ width: percent }} />
              </div>
              <span className={`w-11 text-right font-semibold ${text}`}>{percent}</span>
            </div>
          )
        })}
      </div>

      {/* The buy controls are a picture of the real trading panel: nothing to
          click, so they are plain spans rather than buttons, and hidden from
          assistive technology so nobody is offered an action this page cannot
          perform. */}
      <div aria-hidden className="mb-4 flex gap-2 text-[13px] font-bold">
        <span className="flex-1 rounded-lg border border-success/25 bg-success/10 py-[9px] text-center text-success">Buy YES</span>
        <span className="flex-1 rounded-lg border border-danger/20 bg-danger/8 py-[9px] text-center text-danger">Buy NO</span>
      </div>

      <p className="rounded-lg bg-surface-muted px-3 py-[9px] text-xs text-muted">
        An example market. The question and the prices are made up.
      </p>
    </div>
  )
}
