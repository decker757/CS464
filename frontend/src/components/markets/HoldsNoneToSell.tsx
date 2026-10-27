import Button from '../ui/Button'

interface HoldsNoneToSellProps {
  /** True once the trader has chosen Sell on an outcome they hold none of. */
  holdsNone: boolean
  outcomeLabel: string
  /** Switches the ticket to a buy of this outcome, and focuses the quantity. */
  onBuyInstead: () => void
}

// What a ticket says instead of offering a form, when the trader has chosen
// Sell on an outcome they hold none of ([T-2] #451, [T-6] #496).
//
// Both tickets show it: filling in a sell of shares you do not have is four
// steps to a refusal the screen could have named at the first one. The market
// ticket carried this inline from #478; the limit ticket needed the same
// thing, so it is one component rather than two copies.
//
// **The sentence goes in the caller's own live region, and the caller renders
// that region whether or not there is anything to say.** A screen reader
// announces a live region whose content changes and may not announce one that
// arrives with its content already in it — and each ticket has exactly one
// such region, shared with its "placed" and "traded" lines, so this cannot
// bring a second one of its own.
export default function HoldsNoneToSell({ holdsNone, outcomeLabel, onBuyInstead }: HoldsNoneToSellProps) {
  if (!holdsNone) return null

  return (
    <>
      <p className="mb-3 text-sm text-smu-navy">You don't hold any {outcomeLabel} shares.</p>
      <Button variant="primary" size="block" onClick={onBuyInstead}>
        Buy {outcomeLabel} instead
      </Button>
    </>
  )
}
