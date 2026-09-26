import { Link } from 'react-router-dom'
import type { PublicMarketSummary } from '../../api/marketApi'
import { NAV } from '../../theme/colors'

const STATUS_CONFIG: Record<PublicMarketSummary['status'], { label: string; color: string; bg: string; border: string }> = {
  open:               { label: 'Open',    color: '#16a34a', bg: 'rgba(22,163,74,0.1)',   border: 'rgba(22,163,74,0.25)' },
  closed:             { label: 'Closed',  color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.25)' },
  pending_resolution: { label: 'Pending', color: '#d97706', bg: 'rgba(217,119,6,0.1)',   border: 'rgba(217,119,6,0.25)' },
  approved:           { label: 'Settled', color: '#2563eb', bg: 'rgba(37,99,235,0.1)',   border: 'rgba(37,99,235,0.25)' },
}

function formatCloseTime(iso: string): string {
  const d = new Date(iso)
  if (d <= new Date()) return 'Closed'
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
            {formatCloseTime(market.close_time)}
          </span>
        </div>
        <p style={{ fontSize: 15, fontWeight: 600, color: NAV, lineHeight: 1.5, margin: 0 }}>
          {market.question}
        </p>
      </div>
    </Link>
  )
}
