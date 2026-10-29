import { formatPrice } from '../../utils/formatPrice'
import { movementLabel, type PriceMovement } from './priceMovement'

interface OutcomePriceTextProps {
  label: string
  /** Undefined while the price is on its way, or if it could not be read. */
  price: string | undefined
  movement: PriceMovement | undefined
  className: string
}

// An outcome's price on a browse card, with its cue when it has just moved.
// The arrow is the cue that is not colour, and the label says it in words for
// a trader who hears the card instead (#444 AC 3). It takes its colour from
// whatever it sits in: beside a green Yes, a red ▼ would argue with the
// side's own colour about what red means, and the shape already says which
// way (#441).
export default function OutcomePriceText({ label, price, movement, className }: OutcomePriceTextProps) {
  return (
    <span aria-label={movement ? movementLabel(label, movement) : `${label} price`} className={`flex items-center gap-1 font-bold ${className}`}>
      {movement && <span aria-hidden="true">{movement === 'up' ? '▲' : '▼'}</span>}
      {price ? formatPrice(price) : '—'}
    </span>
  )
}
