import { formatPrice } from '../../utils/formatPrice'
import { SIDE_TONE_FILL, type PricedSide } from './outcomeSides'

interface PriceSplitBarProps {
  sides: PricedSide[]
  /** The bar's height, which is all that differs between its two uses. */
  className: string
}

// One bar split at a market's prices, a segment per outcome in order ([X-1]
// #441): a lopsided market looks lopsided from across the room, and a coin
// flip looks like one.
//
// Each segment's share is its price, handed to flex-grow as the decimal the
// ledger sent, so a share is never parsed or rounded on the way. The basis is
// zero and there is no minimum, so the split is the true proportion from 0 to
// 100%: a floor wide enough for a label flattened 95/5 into 69/31, which is
// why the labels sit above the bar rather than inside it. The grow animates,
// so a trade slides the split rather than snapping it.
//
// An image with a name, because that is what it is: the name is the split in
// words, for a trader who cannot see it.
export default function PriceSplitBar({ sides, className }: PriceSplitBarProps) {
  const description = sides.map(side => `${side.outcome.label} ${formatPrice(side.price)}`).join(', ')
  return (
    <div role="img" aria-label={description} className={`flex overflow-hidden rounded-full ${className}`}>
      {sides.map(side => (
        <div
          key={side.outcome.id}
          style={{ flexGrow: side.price }}
          className={`basis-0 transition-[flex-grow] duration-500 motion-reduce:transition-none ${SIDE_TONE_FILL[side.tone]}`}
        />
      ))}
    </div>
  )
}
