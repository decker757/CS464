import { TrendIcon } from './icons'

interface LogoProps {
  iconSize?: number
  /** Text colour and size, e.g. `text-white text-xl`. */
  className?: string
  /** Classes for the name beside the icon: the signed-in bar hides it from
   * sight on a phone, and keeps it for a screen reader (#438). */
  wordmarkClassName?: string
}

export default function Logo({ iconSize = 20, className = '', wordmarkClassName }: LogoProps) {
  return (
    <span className={`inline-flex items-center gap-2 font-bold tracking-[-0.3px] ${className}`}>
      <TrendIcon size={iconSize} className="text-smu-gold" />
      <span className={wordmarkClassName}>PredictSMU</span>
    </span>
  )
}
