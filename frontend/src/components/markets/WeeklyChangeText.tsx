import { isNegativeCredits, unsignedCredits } from '../../utils/formatCredits'
import { formatPercentagePoints } from '../../utils/formatPrice'

interface WeeklyChangeTextProps {
  /** The outcome's label, for the words a screen reader hears. */
  label: string
  /** Its price now minus its price a week ago; undefined when there is none. */
  change: string | undefined
  className: string
}

// The window, written once, so what the title says and what a screen reader
// hears cannot drift apart.
const WINDOW = 'over the last 7 days'

// One outcome's change in price over the week, on a browse card ([X-1] #564):
// `▲ +12.0 pts`. The direction is the arrow and the sign, never colour alone —
// on a card whose sides are already green and red, a coloured change would
// argue with the side about what the colour means (#441). The label says it
// in words.
//
// Nothing at all without a change to show, which is how a market with no
// trade a week old reads: not a zero, and not a change from its seed price.
export default function WeeklyChangeText({ label, change, className }: WeeklyChangeTextProps) {
  if (change === undefined) return null

  const points = formatPercentagePoints(unsignedCredits(change))
  // A change that rounds away is no change on a card that shows one decimal
  // place: "▲ +0.0 pts" would point a direction at nothing.
  if (points === '0.0') {
    return (
      <span aria-label={`${label} unchanged ${WINDOW}`} title={`No change ${WINDOW}`} className={`whitespace-nowrap ${className}`}>
        No change
      </span>
    )
  }

  const isDown = isNegativeCredits(change)
  return (
    <span
      aria-label={`${label} ${isDown ? 'down' : 'up'} ${points} points ${WINDOW}`}
      title={`Change ${WINDOW}`}
      className={`whitespace-nowrap ${className}`}
    >
      <span aria-hidden="true">{isDown ? '▼' : '▲'}</span> {isDown ? '-' : '+'}{points} pts
    </span>
  )
}
