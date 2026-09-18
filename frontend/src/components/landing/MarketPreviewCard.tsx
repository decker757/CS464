import { GOLD, NAV } from '../../theme/colors'

const SPARKLINE_POINTS: [number, number][] = [
  [0, 52], [12, 50], [24, 48], [36, 51], [48, 55],
  [60, 58], [72, 56], [84, 60], [96, 62], [108, 64],
]

function Sparkline() {
  const w = 108
  const h = 36
  const ys = SPARKLINE_POINTS.map((p) => p[1])
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const scaleY = (y: number) => h - ((y - minY) / (maxY - minY)) * h

  const pathD = SPARKLINE_POINTS.map(([x, y], i) =>
    `${i === 0 ? 'M' : 'L'} ${x} ${scaleY(y)}`
  ).join(' ')
  const areaD = `${pathD} L ${w} ${h} L 0 ${h} Z`
  const last = SPARKLINE_POINTS[SPARKLINE_POINTS.length - 1]

  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: 'block', overflow: 'visible' }}>
      <defs>
        <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#16a34a" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#16a34a" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaD} fill="url(#sparkGrad)" />
      <path d={pathD} fill="none" stroke="#16a34a" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={scaleY(last[1])} r="3" fill="#16a34a" />
    </svg>
  )
}

export default function MarketPreviewCard() {
  return (
    <div
      style={{
        backgroundColor: '#fff',
        borderRadius: 16,
        border: `1px solid rgba(168,134,74,0.3)`,
        borderTop: `3px solid ${GOLD}`,
        boxShadow: '0 12px 48px rgba(21,30,85,0.13)',
        padding: '24px 24px 20px',
        width: '100%',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <span style={{ fontSize: 10, fontWeight: 700, color: GOLD, backgroundColor: 'rgba(168,134,74,0.1)', padding: '3px 9px', borderRadius: 20, textTransform: 'uppercase', letterSpacing: '0.6px' }}>
          Open · Closes 30 Oct
        </span>
        <span style={{ fontSize: 11, color: '#6b7280', display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', backgroundColor: '#16a34a', display: 'inline-block' }} />
          Live
        </span>
      </div>

      <p style={{ fontSize: 14, fontWeight: 600, color: NAV, lineHeight: 1.45, marginBottom: 16 }}>
        Will SMU's handball team win SUNIG this year?
      </p>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: 14 }}>
        <div>
          <p style={{ fontSize: 10, color: '#9ca3af', marginBottom: 4 }}>YES · 7-day trend</p>
          <Sparkline />
        </div>
        <div style={{ textAlign: 'right' }}>
          <p style={{ fontSize: 10, color: '#9ca3af', marginBottom: 2 }}>Current</p>
          <p style={{ fontSize: 22, fontWeight: 800, color: '#16a34a', lineHeight: 1 }}>64¢</p>
          <p style={{ fontSize: 11, color: '#16a34a' }}>▲ +4¢ today</p>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
        {([{ label: 'YES', pct: 64, color: '#16a34a' }, { label: 'NO', pct: 36, color: '#dc2626' }] as const).map(({ label, pct, color }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 12, fontWeight: 700, color, width: 28 }}>{label}</span>
            <div style={{ flex: 1, height: 5, backgroundColor: '#f3f4f6', borderRadius: 99, overflow: 'hidden' }}>
              <div style={{ width: `${pct}%`, height: '100%', backgroundColor: color, borderRadius: 99 }} />
            </div>
            <span style={{ fontSize: 12, fontWeight: 600, color, width: 28, textAlign: 'right' }}>{pct}%</span>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <button style={{ flex: 1, padding: '9px 0', backgroundColor: 'rgba(22,163,74,0.1)', color: '#16a34a', fontWeight: 700, fontSize: 13, border: '1px solid rgba(22,163,74,0.25)', borderRadius: 8, cursor: 'default' }}>
          Buy YES · 64¢
        </button>
        <button style={{ flex: 1, padding: '9px 0', backgroundColor: 'rgba(220,38,38,0.08)', color: '#dc2626', fontWeight: 700, fontSize: 13, border: '1px solid rgba(220,38,38,0.2)', borderRadius: 8, cursor: 'default' }}>
          Buy NO · 36¢
        </button>
      </div>

      <div style={{ backgroundColor: '#fafafa', borderRadius: 8, padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 24, height: 24, borderRadius: '50%', backgroundColor: 'rgba(21,30,85,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, color: NAV, flexShrink: 0 }}>
          S
        </div>
        <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>
          <strong style={{ color: NAV }}>sarah_lim</strong> bought 20 YES shares
          <span style={{ color: '#9ca3af' }}> · just now</span>
        </p>
      </div>
    </div>
  )
}
