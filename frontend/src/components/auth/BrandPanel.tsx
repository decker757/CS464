import { GOLD, NAV } from '../../theme/colors'
import { TrendIcon } from './AuthIcons'

const BENEFITS = [
  'Trade with risk-free mock credits',
  'Explore live prediction markets',
  'Compete on the community leaderboard',
]

export default function BrandPanel() {
  return (
    <div
      className="hidden md:flex"
      style={{
        width: '42%',
        minHeight: '100vh',
        backgroundColor: NAV,
        flexDirection: 'column',
        justifyContent: 'space-between',
        padding: '40px 48px',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Dot grid */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: 'radial-gradient(circle, rgba(255,255,255,0.06) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
          pointerEvents: 'none',
        }}
      />

      {/* Content */}
      <div style={{ position: 'relative' }}>
        <a
          href="/"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 8, textDecoration: 'none', marginBottom: 56 }}
        >
          <TrendIcon size={20} color={GOLD} />
          <span style={{ color: '#fff', fontSize: 19, fontWeight: 700, letterSpacing: '-0.3px' }}>PredictSMU</span>
        </a>

        <h2
          style={{
            color: '#fff',
            fontSize: 'clamp(26px, 3vw, 36px)',
            fontWeight: 800,
            lineHeight: 1.2,
            letterSpacing: '-0.5px',
            marginBottom: 16,
          }}
        >
          Predict what happens next.
        </h2>
        <p style={{ color: 'rgba(255,255,255,0.6)', fontSize: 15, lineHeight: 1.65, marginBottom: 48, maxWidth: 320 }}>
          Join the SMU community, trade on real-world outcomes with mock credits, and climb the leaderboard.
        </p>

        <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {BENEFITS.map((b) => (
            <li key={b} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: '50%',
                  backgroundColor: 'rgba(168,134,74,0.2)',
                  border: '1px solid rgba(168,134,74,0.5)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                }}
              >
                <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden>
                  <polyline points="1.5,5 4,7.5 8.5,2" stroke={GOLD} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <span style={{ color: 'rgba(255,255,255,0.82)', fontSize: 14 }}>{b}</span>
            </li>
          ))}
        </ul>
      </div>

      <p style={{ position: 'relative', color: 'rgba(255,255,255,0.45)', fontSize: 12 }}>
        PredictSMU · CS464
      </p>
    </div>
  )
}
