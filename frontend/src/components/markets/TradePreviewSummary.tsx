import type { TradePreview, TradeSide } from '../../api/ledgerApi'
import { formatAveragePrice, formatCreditsPrecise, signedAmountColor, subtractCredits, unsignedCredits } from '../../utils/formatCredits'
import { formatPrice } from '../../utils/formatPrice'

interface TradePreviewSummaryProps {
  preview: TradePreview
  side: TradeSide
  outcomeId: string
  outcomeLabel: string
}

// A share pays exactly 1 credit, so what a winning buy pays out is the
// quantity the server quoted — a relabel of its own figure, never a sum of
// ours. The profit is that payout minus the cost it charged, subtracted
// exactly on the digits (formatCredits.ts). Null when either string is not a
// plain decimal, and then the row is dropped: a dash does not belong where a
// credits figure does.
function profitIfItWins(preview: TradePreview): string | null {
  return subtractCredits(preview.quantity, unsignedCredits(preview.total))
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mt-1 flex justify-between first:mt-0">
      <span className="text-muted">{label}</span>
      {children}
    </div>
  )
}

/**
 * What the quote says, every figure of it the server's own ([T-1] #21): the
 * cost or proceeds, what a winning buy would pay and leave over, the average
 * fill price, and where this trade moves the outcome's price.
 *
 * Only a buy shows a payout. A sell hands the shares back, so there is
 * nothing left to win on them, and a "pays if it wins" line beside the
 * proceeds would promise the trader both (#357).
 */
export default function TradePreviewSummary({ preview, side, outcomeId, outcomeLabel }: TradePreviewSummaryProps) {
  const profit = profitIfItWins(preview)
  const priceNow = preview.prices.find(p => p.outcome_id === outcomeId)
  const priceAfter = preview.post_trade_prices.find(p => p.outcome_id === outcomeId)

  return (
    <div aria-label="trade preview" className="mb-3 rounded-control bg-smu-cream px-4 py-3 text-[13px]">
      <Row label={side === 'buy' ? 'Cost' : 'Proceeds'}>
        <strong className="text-smu-navy">{formatCreditsPrecise(unsignedCredits(preview.total))} credits</strong>
      </Row>
      {side === 'buy' && (
        <>
          <Row label="Pays if it wins">
            <span className="text-smu-navy">{formatCreditsPrecise(preview.quantity)} credits</span>
          </Row>
          {profit !== null && (
            <Row label="Profit if it wins">
              <span className={signedAmountColor(profit)}>{formatCreditsPrecise(profit, { showSign: true })} credits</span>
            </Row>
          )}
        </>
      )}
      {/* The unit is on the figure, the way the credits rows above carry
          theirs. An average price is credits per share and not a probability
          (#247), but a bare decimal between a credits row and a percentage row
          reads as one — so this row says which it is (#430). */}
      <Row label="Average price">
        <span className="text-smu-navy">{formatAveragePrice(preview.average_price)} credits/share</span>
      </Row>
      {priceNow && priceAfter && (
        <Row label={`${outcomeLabel} price`}>
          <span className="text-smu-navy">{formatPrice(priceNow.price)} → {formatPrice(priceAfter.price)}</span>
        </Row>
      )}
    </div>
  )
}
