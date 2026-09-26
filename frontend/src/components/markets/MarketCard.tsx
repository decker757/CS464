import { Link } from 'react-router-dom'
import type { PublicMarketSummary } from '../../api/marketApi'
import { NAV } from '../../theme/colors'
import { STATUS_CONFIG, tradingStopped } from './marketStatus'

function formatCloseTime(market: PublicMarketSummary): string {
  if (tradingStopped(market.status, market.close_time)) return 'Closed'
  const d = new Date(market.close_time)
  return `Closes ${d.toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' })}`
}

export default function MarketCard({ market }: { market: PublicMarketSummary }) {
  const { label, color, bg, border } = STATUS_CONFIG[market.status]

  return (
    <Link to={`/markets/${market.id}`} style={{ textDecoration: 'none', display: 'block' }}>
      <div
        style={{
          backgroundColor: '#fff',
          border: '1px solid rgba(182,145,70,0.15)',
          borderRadius: 16,
          padding: '20px 24px',
          boxShadow: '0 2px 8px rgba(21,30,85,0.05)',
          transition: 'box-shadow 0.15s, border-color 0.15s',
          cursor: 'pointer',
          height: '100%',
          boxSizing: 'border-box',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.boxShadow = '0 4px 20px rgba(21,30,85,0.10)'
          e.currentTarget.style.borderColor = 'rgba(182,145,70,0.35)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.boxShadow = '0 2px 8px rgba(21,30,85,0.05)'
          e.currentTarget.style.borderColor = 'rgba(182,145,70,0.15)'
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, gap: 8 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              color,
              backgroundColor: bg,
              border: `1px solid ${border}`,
              padding: '3px 10px',
              borderRadius: 20,
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
              flexShrink: 0,
            }}
          >
            {label}
          </span>
          <span style={{ fontSize: 12, color: '#9ca3af', textAlign: 'right' }}>
            {formatCloseTime(market)}
          </span>
        </div>
        <p style={{ fontSize: 15, fontWeight: 600, color: NAV, lineHeight: 1.5, margin: 0 }}>
          {market.question}
        </p>
      </div>
    </Link>
  )
}
