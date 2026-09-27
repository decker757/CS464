import { buttonClass } from '../ui/buttonClass'
import Eyebrow from '../ui/Eyebrow'
import MarketPreviewCard from './MarketPreviewCard'

const LEADERS = [
  { rank: '#1', name: 'ernest_t', credits: '$2,340' },
  { rank: '#2', name: 'michelle_t', credits: '$1,580' },
]

/** A small card pinned to a corner of the preview. Pass its position in `className`. */
function FloatingCard({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <div className={`absolute z-[2] rounded-xl border border-smu-gold/25 bg-white px-4 py-3 shadow-float ${className}`}>
      {children}
    </div>
  )
}

function PortfolioMiniCard() {
  return (
    <FloatingCard className="-bottom-6 -left-8 w-40">
      <p className="mb-1 text-[10px] text-subtle">My Portfolio</p>
      <p className="text-lg leading-none font-extrabold text-success">+12.4%</p>
      <p className="mt-0.5 text-[11px] text-muted">$1,580 net worth</p>
    </FloatingCard>
  )
}

function LeaderboardMiniCard() {
  return (
    <FloatingCard className="-top-5 -right-6 w-[168px]">
      <p className="mb-1.5 text-[10px] text-subtle">🏆 Leaderboard</p>
      {LEADERS.map(({ rank, name, credits }) => (
        <div key={rank} className="mb-1 flex items-center justify-between text-[11px]">
          <span className="w-5 font-bold text-smu-gold">{rank}</span>
          <span className="flex-1 pl-1 font-semibold text-smu-navy">{name}</span>
          <span className="text-muted">{credits}</span>
        </div>
      ))}
    </FloatingCard>
  )
}

export default function LandingHero() {
  return (
    <section className="relative overflow-hidden">
      <div aria-hidden className="dot-grid-on-cream pointer-events-none absolute inset-0" />
      <div aria-hidden className="pointer-events-none absolute top-[10%] right-[5%] size-[480px] rounded-full bg-radial from-smu-gold/12 to-transparent to-70%" />

      <div className="relative mx-auto grid max-w-[1200px] grid-cols-[repeat(auto-fit,minmax(300px,1fr))] items-center gap-16 px-8 pt-[clamp(40px,6vw,72px)] pb-[clamp(60px,8vw,96px)]">
        <div>
          <Eyebrow className="mb-4 text-[11px]">SMU's Prediction Market</Eyebrow>

          <h1 className="mb-5 text-[clamp(32px,4.5vw,52px)] leading-[1.15] font-extrabold tracking-[-1px] text-smu-navy">
            Trade on what you think{' '}
            <span className="relative inline-block text-smu-gold">
              happens next.
              <span aria-hidden className="absolute bottom-0.5 left-0 h-[3px] w-full rounded-sm bg-smu-gold opacity-35" />
            </span>
          </h1>

          <p className="mb-9 max-w-[460px] text-[clamp(15px,1.8vw,17px)] leading-[1.65] text-secondary">
            Explore real-world questions, trade YES or NO shares with mock credits, and compete with the SMU community.
          </p>

          <div className="flex flex-wrap gap-3">
            <a href="/register" className={buttonClass('primary', 'md')}>
              Explore Markets
              <span className="text-base font-bold text-smu-gold">→</span>
            </a>
            <a href="#how-it-works" className={buttonClass('outline', 'md')}>
              How It Works
            </a>
          </div>
        </div>

        <div className="flex items-center justify-center">
          <div className="relative w-full max-w-[380px] pt-6 pb-8">
            <div aria-hidden className="pointer-events-none absolute top-1/2 left-1/2 z-0 h-4/5 w-[90%] -translate-1/2 rounded-3xl bg-radial from-smu-gold/18 to-transparent to-70% blur-[20px]" />
            <div className="relative z-[3] origin-bottom -rotate-[1.5deg] drop-shadow-hero">
              <MarketPreviewCard />
            </div>
            <LeaderboardMiniCard />
            <PortfolioMiniCard />
          </div>
        </div>
      </div>
    </section>
  )
}
