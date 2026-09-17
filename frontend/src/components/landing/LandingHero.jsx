import MarketPreviewCard from './MarketPreviewCard'

const NAV = '#151E55'
const GOLD = '#A8864A'

function PortfolioMiniCard() {
  return (
    <div
      style={{
        position: 'absolute',
        bottom: -24,
        left: -32,
        backgroundColor: '#fff',
        borderRadius: 12,
        border: `1px solid rgba(168,134,74,0.25)`,
        boxShadow: '0 8px 24px rgba(21,30,85,0.12)',
        padding: '12px 16px',
        width: 160,
        zIndex: 2,
      }}
    >
      <p style={{ fontSize: 10, color: '#9ca3af', marginBottom: 4 }}>My Portfolio</p>
      <p style={{ fontSize: 18, fontWeight: 800, color: '#16a34a', lineHeight: 1 }}>+12.4%</p>
      <p style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>$1,580 net worth</p>
    </div>
  )
}

function LeaderboardMiniCard() {
  return (
    <div
      style={{
        position: 'absolute',
        top: -20,
        right: -24,
        backgroundColor: '#fff',
        borderRadius: 12,
        border: `1px solid rgba(168,134,74,0.25)`,
        boxShadow: '0 8px 24px rgba(21,30,85,0.12)',
        padding: '12px 16px',
        width: 168,
        zIndex: 2,
      }}
    >
      <p style={{ fontSize: 10, color: '#9ca3af', marginBottom: 6 }}>🏆 Leaderboard</p>
      {[
        { rank: '#1', name: 'ernest_t', cr: '$2,340' },
        { rank: '#2', name: 'michelle_t', cr: '$1,580' },
      ].map(({ rank, name, cr }) => (
        <div key={rank} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <span style={{ fontSize: 11, color: GOLD, fontWeight: 700, width: 20 }}>{rank}</span>
          <span style={{ fontSize: 11, color: NAV, fontWeight: 600, flex: 1, paddingLeft: 4 }}>{name}</span>
          <span style={{ fontSize: 11, color: '#6b7280' }}>{cr}</span>
        </div>
      ))}
    </div>
  )
}

export default function LandingHero() {
  return (
    <section
      style={{
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Dot grid background */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: 'radial-gradient(circle, rgba(21,30,85,0.07) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
          pointerEvents: 'none',
        }}
      />
      {/* Gold radial glow — right side */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          top: '10%',
          right: '5%',
          width: 480,
          height: 480,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(168,134,74,0.12) 0%, transparent 70%)',
          pointerEvents: 'none',
        }}
      />

      <div
        style={{
          position: 'relative',
          maxWidth: 1200,
          margin: '0 auto',
          padding: 'clamp(40px, 6vw, 72px) 32px clamp(60px, 8vw, 96px)',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
          gap: 64,
          alignItems: 'center',
        }}
      >
        {/* Left — copy */}
        <div>
          <span
            style={{
              display: 'inline-block',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '1.2px',
              color: GOLD,
              textTransform: 'uppercase',
              marginBottom: 16,
            }}
          >
            SMU's Prediction Market
          </span>

          <h1
            style={{
              fontSize: 'clamp(32px, 4.5vw, 52px)',
              fontWeight: 800,
              color: NAV,
              lineHeight: 1.15,
              letterSpacing: '-1px',
              marginBottom: 20,
            }}
          >
            Trade on what you think{' '}
            <span
              style={{
                position: 'relative',
                display: 'inline-block',
                color: GOLD,
              }}
            >
              happens next.
              <span
                aria-hidden
                style={{
                  position: 'absolute',
                  left: 0,
                  bottom: 2,
                  width: '100%',
                  height: 3,
                  backgroundColor: GOLD,
                  borderRadius: 2,
                  opacity: 0.35,
                }}
              />
            </span>
          </h1>

          <p
            style={{
              fontSize: 'clamp(15px, 1.8vw, 17px)',
              color: '#4b5563',
              lineHeight: 1.65,
              marginBottom: 36,
              maxWidth: 460,
            }}
          >
            Explore real-world questions, trade YES or NO shares with mock credits, and compete with the SMU community.
          </p>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <a
              href="/register"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                padding: '13px 26px',
                backgroundColor: NAV,
                color: '#fff',
                fontWeight: 600,
                fontSize: 15,
                borderRadius: 10,
                textDecoration: 'none',
                transition: 'opacity 0.15s, box-shadow 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.opacity = '0.9'
                e.currentTarget.style.boxShadow = `0 0 0 3px rgba(168,134,74,0.35)`
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.opacity = '1'
                e.currentTarget.style.boxShadow = 'none'
              }}
            >
              Explore Markets
              <span style={{ color: GOLD, fontSize: 16, fontWeight: 700 }}>→</span>
            </a>
            <a
              href="#how-it-works"
              style={{
                display: 'inline-block',
                padding: '13px 26px',
                backgroundColor: 'transparent',
                color: NAV,
                fontWeight: 600,
                fontSize: 15,
                borderRadius: 10,
                textDecoration: 'none',
                border: `1.5px solid rgba(21,30,85,0.22)`,
                transition: 'border-color 0.15s, background 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = NAV
                e.currentTarget.style.background = 'rgba(21,30,85,0.04)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'rgba(21,30,85,0.22)'
                e.currentTarget.style.background = 'transparent'
              }}
            >
              How It Works
            </a>
          </div>
        </div>

        {/* Right — layered card composition */}
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center' }}>
          <div style={{ position: 'relative', width: '100%', maxWidth: 380, paddingTop: 24, paddingBottom: 32 }}>
            {/* Gold glow behind main card */}
            <div
              aria-hidden
              style={{
                position: 'absolute',
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
                width: '90%',
                height: '80%',
                borderRadius: 24,
                background: `radial-gradient(ellipse, rgba(168,134,74,0.18) 0%, transparent 70%)`,
                filter: 'blur(20px)',
                pointerEvents: 'none',
                zIndex: 0,
              }}
            />

            {/* Main card — slightly rotated */}
            <div
              style={{
                position: 'relative',
                zIndex: 3,
                transform: 'rotate(-1.5deg)',
                transformOrigin: 'center bottom',
                filter: 'drop-shadow(0 16px 40px rgba(21,30,85,0.14))',
              }}
            >
              <MarketPreviewCard />
            </div>

            {/* Leaderboard mini card — upper right */}
            <LeaderboardMiniCard />

            {/* Portfolio mini card — lower left */}
            <PortfolioMiniCard />
          </div>
        </div>
      </div>
    </section>
  )
}
