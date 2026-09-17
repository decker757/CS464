const NAV = '#151E55'
const GOLD = '#A8864A'

const FEATURES = [
  {
    num: '01',
    title: 'Browse Markets',
    desc: 'Explore open prediction markets on real-world and campus events.',
  },
  {
    num: '02',
    title: 'Trade with Credits',
    desc: 'Buy and sell YES or NO shares using risk-free mock credits.',
  },
  {
    num: '03',
    title: 'Climb the Leaderboard',
    desc: 'Grow your portfolio and compete with other predictors.',
  },
]

export default function LandingFeatures() {
  return (
    <section
      id="how-it-works"
      style={{
        maxWidth: 1200,
        margin: '0 auto',
        padding: '0 32px 96px',
      }}
    >
      <p style={{ fontSize: 12, fontWeight: 700, color: GOLD, textTransform: 'uppercase', letterSpacing: '1px', marginBottom: 12 }}>
        How it works
      </p>
      <h2 style={{ fontSize: 'clamp(20px, 2.5vw, 26px)', fontWeight: 700, color: NAV, marginBottom: 32 }}>
        Everything you need to make your prediction
      </h2>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
          gap: 20,
        }}
      >
        {FEATURES.map((f) => (
          <FeatureCard key={f.num} {...f} />
        ))}
      </div>
    </section>
  )
}

function FeatureCard({ num, title, desc }) {
  return (
    <div
      style={{
        backgroundColor: '#fff',
        border: `1px solid rgba(168,134,74,0.2)`,
        borderRadius: 14,
        padding: '22px 24px 26px',
        boxShadow: '0 2px 8px rgba(21,30,85,0.05)',
        transition: 'transform 0.18s, box-shadow 0.18s',
        cursor: 'default',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = 'translateY(-4px)'
        e.currentTarget.style.boxShadow = '0 8px 24px rgba(21,30,85,0.10)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = 'translateY(0)'
        e.currentTarget.style.boxShadow = '0 2px 8px rgba(21,30,85,0.05)'
      }}
    >
      <span
        style={{
          display: 'inline-block',
          fontSize: 12,
          fontWeight: 700,
          color: GOLD,
          backgroundColor: 'rgba(168,134,74,0.1)',
          padding: '4px 10px',
          borderRadius: 6,
          letterSpacing: '0.5px',
          marginBottom: 16,
        }}
      >
        {num}
      </span>
      <h3 style={{ fontSize: 17, fontWeight: 700, color: NAV, marginBottom: 8 }}>{title}</h3>
      <p style={{ fontSize: 14, color: '#6b7280', lineHeight: 1.6 }}>{desc}</p>
    </div>
  )
}
