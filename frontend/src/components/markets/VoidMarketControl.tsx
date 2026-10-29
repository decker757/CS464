import Button from '../ui/Button'
import ReasonModal from '../ui/ReasonModal'
import type { useVoidMarket } from '../../hooks/useVoidMarket'

interface VoidMarketControlProps {
  marketId: string
  // The server's `voidable`, judged on the stored status.
  voidable: boolean
  isVoided: boolean
  voidAction: ReturnType<typeof useVoidMarket>
}

// Void (#526): the row's Void market button, its reason, and Finish for a
// void whose refunds went through but market_service was not told.
export default function VoidMarketControl({ marketId, voidable, isVoided, voidAction }: VoidMarketControlProps) {
  // `voidable` is the server's answer, and the control never works it out
  // (market-service.md's void-screen notes, point 1). A voided market can
  // read voidable — one marked voided by a direct call to market_service —
  // and still gets no control here: this screen builds no repeat but Finish
  // (ADR 0025, "Known edges, accepted").
  const canVoid = voidable && !isVoided && !voidAction.superseded && !voidAction.withdrawn && !voidAction.awaitingFinish

  return (
    <>
      {canVoid && (
        <Button variant="outline" size="xs" onClick={voidAction.openVoidModal}>
          Void market
        </Button>
      )}
      {voidAction.awaitingFinish && !isVoided && (
        <Button variant="outline" size="xs" onClick={voidAction.handleVoid} disabled={voidAction.submitting}>
          {voidAction.submitting ? 'Finishing…' : 'Finish'}
        </Button>
      )}
      {voidAction.showVoidModal && (
        <ReasonModal
          ariaLabel="Void market"
          title="Void this market?"
          description="This cannot be undone. The market ends with no winner, and every holder is refunded what their remaining shares cost. The reason is shown to traders and goes into the admin log."
          fieldId={`void-reason-${marketId}`}
          reason={voidAction.reason}
          onReasonChange={voidAction.setReason}
          reasonError={voidAction.reasonError}
          placeholder="Explain why this market has no correct answer."
          canConfirm={voidAction.canConfirm}
          isSubmitting={voidAction.submitting}
          confirmLabel="Confirm Void"
          confirmingLabel="Voiding…"
          onCancel={voidAction.closeVoidModal}
          onConfirm={voidAction.handleVoid}
        />
      )}
    </>
  )
}
