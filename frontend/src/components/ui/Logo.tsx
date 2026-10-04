import { TrendIcon } from './icons'

interface LogoProps {
  iconSize?: number
  /** Text colour and size, e.g. `text-white text-xl`. */
  className?: string
}

export default function Logo({ iconSize = 20, className = '' }: LogoProps) {
  return (
    <span className={`inline-flex items-center gap-2 font-bold tracking-[-0.3px] ${className}`}>
      <TrendIcon size={iconSize} className="text-smu-gold" />
      PredictSMU
    </span>
  )
}
