import { TrendIcon } from './icons'

interface LogoProps {
  iconSize?: number
  /** Text colour and size, e.g. `text-white text-xl`. */
  className?: string
  /** Classes for the name beside the icon. The signed-in bar hides it from
   * sight on a phone (#438); a screen reader is not relying on it there,
   * because the home link's `aria-label` replaces the logo's own text as the
   * link's name. */
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
