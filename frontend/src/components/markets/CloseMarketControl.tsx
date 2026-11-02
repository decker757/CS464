import Button from '../ui/Button'
import ReasonModal from '../ui/ReasonModal'
import type { useCloseMarket } from '../../hooks/useCloseMarket'

interface CloseMarketControlProps {
  marketId: string
  // Whether the market still trades: open, and its close_time not yet passed
  // (market-service.md's notes for #56, point 1).
  canClose: boolean
  closeAction: ReturnType<typeof useCloseMarket>
}

// Close early ([2.3] #7): the row's Close button and the reason it asks for.
export default function CloseMarketControl({ marketId, canClose, closeAction }: CloseMarketControlProps) {
  return (
    <>
      {canClose && !closeAction.superseded && (
        <Button variant="outline" size="xs" onClick={closeAction.openCloseModal}>
          Close
        </Button>
      )}
      {closeAction.showCloseModal && (
        <ReasonModal
          ariaLabel="Close market"
          title="Close this market early?"
          description="Trading stops immediately and cannot be undone. The reason goes into the admin log and cannot be edited afterwards."
          fieldId={`close-reason-${marketId}`}
          reason={closeAction.reason}
          onReasonChange={closeAction.setReason}
          reasonError={closeAction.reasonError}
          placeholder="Explain why this market is being closed early."
          canConfirm={closeAction.canConfirmClose}
          isSubmitting={closeAction.closing}
          confirmLabel="Confirm Close"
          confirmingLabel="Closing…"
          onCancel={closeAction.closeCloseModal}
          onConfirm={closeAction.handleClose}
        />
      )}
    </>
  )
}
