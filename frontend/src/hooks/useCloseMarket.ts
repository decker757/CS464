import { useState } from 'react'
import { closeMarket } from '../api/marketApi'
import { useReasonAction } from './useReasonAction'

// What the admin should do for each documented 409 (market-service.md,
// "Only an open market"). A refused close writes nothing, so there is no
// state to roll back — just tell the admin and let them reload.
const CODE_MESSAGES: Record<string, string> = {
  market_not_open: 'This market is not published yet, so it cannot be closed.',
  market_closed: 'Somebody else closed this market, or its closing time passed. Reload to see its current state.',
  market_pending_resolution: 'This market has already stopped and has a proposed outcome.',
  market_already_approved: 'This market has already stopped and its outcome is approved.',
  market_already_settled: 'This market has already stopped and been settled. Reload to see its current state.',
  market_already_voided: 'This market has already been voided. Reload to see its current state.',
}

// These mean the market already stopped some other way — reload rather than
// retry (market-service.md's notes for #56, point 6, the close endpoint's
// "Only an open market" table, and its error table for market_already_voided).
const SUPERSEDING_CODES = new Set([
  'market_closed', 'market_pending_resolution', 'market_already_approved', 'market_already_settled', 'market_already_voided',
])

/** Closing a market early ([2.3] #7): the reason popup's state, the close
 * call, and what to show for each documented error. `onClosed` fires once
 * the close succeeds, so the caller can repaint without waiting for the
 * list's next fetch (market-service.md's notes for #56, point 5). */
export function useCloseMarket(marketId: string, onClosed: () => void) {
  const [showCloseModal, setShowCloseModal] = useState(false)

  const { reason, setReason, submitting: closing, formError, reasonError, superseded, canConfirm: canConfirmClose, handleConfirm: handleClose } =
    useReasonAction({
      submit: reason => closeMarket(marketId, reason),
      incompleteCode: 'close_incomplete',
      codeMessages: CODE_MESSAGES,
      supersedingCodes: SUPERSEDING_CODES,
      genericFailureMessage: 'Closing this market failed. Please try again.',
      onDone: () => { setShowCloseModal(false); onClosed() },
      onError: () => setShowCloseModal(false),
    })

  return {
    showCloseModal,
    openCloseModal: () => setShowCloseModal(true),
    closeCloseModal: () => setShowCloseModal(false),
    reason,
    setReason,
    closing,
    formError,
    reasonError,
    superseded,
    canConfirmClose,
    handleClose,
  }
}
