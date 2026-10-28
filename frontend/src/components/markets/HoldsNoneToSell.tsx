import type { ReactNode } from 'react'
import Button from '../ui/Button'

interface HoldsNoneToSellProps {
  /** True once the trader has chosen Sell on an outcome they hold none of. */
  holdsNone: boolean
  outcomeLabel: string
  /** Switches the ticket to a buy of this outcome. */
  onBuyInstead: () => void
  /** Any other line the ticket announces, which shares this live region. */
  children?: ReactNode
}

// What a ticket says instead of offering a form, when the trader has chosen
// Sell on an outcome they hold none of ([T-2] #451, [T-6] #496).
//
// Both tickets show it: filling in a sell of shares you do not have is four
// steps to a refusal the screen could have named at the first one. The market
// ticket carried this inline from #478; the limit ticket needed the same
// thing, so it is one component rather than two copies.
//
// **The live region is rendered whenever the ticket is, on either side, and
// is empty until there is something to say.** A screen reader announces a
// live region whose content changes and may not announce one that arrives
// with its content already in it — and the holding is usually known before
// Sell is pressed, so a region mounted by Sell would arrive with the sentence
// in it. The button sits after the region, as #478 shipped it: the region
// announces the fact, and the offer is an ordinary button.
export default function HoldsNoneToSell({ holdsNone, outcomeLabel, onBuyInstead, children }: HoldsNoneToSellProps) {
  return (
    <>
      <div role="status">
        {children}
        {holdsNone && <p className="mb-3 text-sm text-smu-navy">You don't hold any {outcomeLabel} shares.</p>}
      </div>
      {holdsNone && (
        <Button variant="primary" size="block" onClick={onBuyInstead}>
          Buy {outcomeLabel} instead
        </Button>
      )}
    </>
  )
}
