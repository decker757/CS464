import { CheckIcon } from '../ui/icons'
import Logo from '../ui/Logo'

const BENEFITS = [
  'Trade with risk-free mock credits',
  'Explore live prediction markets',
  'Compete on the community leaderboard',
]

export default function BrandPanel() {
  return (
    <div className="relative hidden min-h-screen w-[42%] flex-col justify-between overflow-hidden bg-smu-navy px-12 py-10 md:flex">
      <div aria-hidden className="dot-grid-on-navy pointer-events-none absolute inset-0" />

      <div className="relative">
        <a href="/" className="mb-14 inline-flex">
          <Logo className="text-[19px] text-white" />
        </a>

        <h2 className="mb-4 text-[clamp(26px,3vw,36px)] leading-[1.2] font-extrabold tracking-[-0.5px] text-white">
          Predict what happens next.
        </h2>
        <p className="mb-12 max-w-[320px] text-[15px] leading-[1.65] text-white/60">
          Join the SMU community, trade on real-world outcomes with mock credits, and climb the leaderboard.
        </p>

        <ul className="flex flex-col gap-4">
          {BENEFITS.map((benefit) => (
            <li key={benefit} className="flex items-center gap-3">
              <span className="flex size-[22px] shrink-0 items-center justify-center rounded-full border border-smu-gold/50 bg-smu-gold/20 text-smu-gold">
                <CheckIcon />
              </span>
              <span className="text-sm text-white/80">{benefit}</span>
            </li>
          ))}
        </ul>
      </div>

      <p className="relative text-xs text-white/45">PredictSMU · CS464</p>
    </div>
  )
}
