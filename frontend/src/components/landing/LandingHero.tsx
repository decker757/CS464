import { Link } from 'react-router-dom'
import { formatCredits } from '../../utils/formatCredits'
import { buttonClass } from '../ui/buttonClass'
import Eyebrow from '../ui/Eyebrow'
import MarketPreviewCard from './MarketPreviewCard'

// Made-up figures for the two cards pinned to the preview. The handles are
// placeholders rather than anybody's real one, and the amounts are decimal
// strings so they are written in credits by the app's own formatter.
const EXAMPLE_LEADERS = [
  { rank: '#1', name: 'trader_one', credits: '2340' },
  { rank: '#2', name: 'trader_two', credits: '1580' },
]
const EXAMPLE_NET_WORTH = '1580'

/**
 * A small card overlapping one edge of the preview. Pass its side and its
 * overlap in `className`.
 *
 * The cards stack above and below the preview in the flow, so nothing can
 * slide under it, and sit in front of it, so the few pixels they share land on
 * the preview's padding and not on either card's text (#435).
 */
function FloatingCard({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <div className={`relative z-[4] rounded-xl border border-smu-gold/25 bg-white px-4 py-3 shadow-float ${className}`}>
      {children}
    </div>
  )
}

function PortfolioMiniCard() {
  return (
    <FloatingCard className="-mt-2 -ml-8 w-40 self-start">
      <p className="mb-1 text-[10px] text-subtle">My Portfolio</p>
      <p className="text-lg leading-none font-extrabold text-success">+12.4%</p>
      <p className="mt-0.5 text-[11px] text-muted">{formatCredits(EXAMPLE_NET_WORTH)} credits</p>
    </FloatingCard>
  )
}

function LeaderboardMiniCard() {
  return (
    <FloatingCard className="-mr-6 -mb-2 w-[196px] self-end">
      <p className="mb-1.5 text-[10px] text-subtle">🏆 Leaderboard</p>
      {EXAMPLE_LEADERS.map(({ rank, name, credits }) => (
        <div key={rank} className="mb-1 flex items-center justify-between text-[11px]">
          <span className="w-5 font-bold text-smu-gold-text">{rank}</span>
          <span className="flex-1 pl-1 font-semibold text-smu-navy">{name}</span>
          <span className="text-muted">{formatCredits(credits)} credits</span>
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
            <Link to="/register" className={buttonClass('primary', 'md')}>
              Explore Markets
              <span className="text-base font-bold text-smu-gold">→</span>
            </Link>
            <a href="#how-it-works" className={buttonClass('outline', 'md')}>
              How It Works
            </a>
          </div>
        </div>

        <div className="flex items-center justify-center">
          <div className="relative flex w-full max-w-[380px] flex-col">
            <div aria-hidden className="pointer-events-none absolute top-1/2 left-1/2 z-0 h-4/5 w-[90%] -translate-1/2 rounded-3xl bg-radial from-smu-gold/18 to-transparent to-70% blur-[20px]" />
            <LeaderboardMiniCard />
            <div className="relative z-[3] origin-bottom -rotate-[1.5deg] drop-shadow-hero">
              <MarketPreviewCard />
            </div>
            <PortfolioMiniCard />
          </div>
        </div>
      </div>
    </section>
  )
}
