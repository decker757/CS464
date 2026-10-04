import Card from '../ui/Card'
import Eyebrow from '../ui/Eyebrow'

const FEATURES = [
  { num: '01', title: 'Browse Markets', desc: 'Explore open prediction markets on real-world and campus events.' },
  { num: '02', title: 'Trade with Credits', desc: 'Buy and sell YES or NO shares using risk-free mock credits.' },
  { num: '03', title: 'Climb the Leaderboard', desc: 'Grow your portfolio and compete with other predictors.' },
]

interface FeatureCardProps {
  num: string
  title: string
  desc: string
}

function FeatureCard({ num, title, desc }: FeatureCardProps) {
  return (
    <Card className="px-6 pt-[22px] pb-[26px] transition hover:-translate-y-1 hover:shadow-card-hover">
      <span className="mb-4 inline-block rounded-md bg-smu-gold/10 px-2.5 py-1 text-xs font-bold tracking-[0.5px] text-smu-gold">
        {num}
      </span>
      <h3 className="mb-2 text-[17px] font-bold text-smu-navy">{title}</h3>
      <p className="text-sm leading-[1.6] text-muted">{desc}</p>
    </Card>
  )
}

export default function LandingFeatures() {
  return (
    <section id="how-it-works" className="mx-auto max-w-[1200px] px-8 pb-24">
      <Eyebrow className="mb-3 text-xs">How it works</Eyebrow>
      <h2 className="mb-8 text-[clamp(20px,2.5vw,26px)] font-bold text-smu-navy">
        Everything you need to make your prediction
      </h2>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-5">
        {FEATURES.map((feature) => <FeatureCard key={feature.num} {...feature} />)}
      </div>
    </section>
  )
}
