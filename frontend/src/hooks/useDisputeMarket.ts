import { useState } from 'react'
import { sendBackOutcome } from '../api/marketApi'
import { useReasonAction } from './useReasonAction'

// What the admin should do for each documented 409
// (market-service.md, send-back-outcome's "When it is refused").
const CODE_MESSAGES: Record<string, string> = {
  dispute_window_closed: 'Too late. The dispute window has closed.',
  proposal_superseded: 'This result was already replaced.',
  market_not_approved: 'Somebody else acted on this market first.',
  market_already_settled: 'Somebody else acted on this market first.',
  market_pending_resolution: 'Somebody else acted on this market first.',
  market_already_voided: 'This market has been voided, so its result is gone.',
}

// All six 409s (five from send-back's own table, plus market_already_voided
// from market-service.md's error table) mean a retry cannot succeed without a
// fresh read of the row — there is no "reason too short" case to recover from in place.
const SUPERSEDING_CODES = new Set([
  'dispute_window_closed', 'proposal_superseded',
  'market_not_approved', 'market_already_settled', 'market_pending_resolution', 'market_already_voided',
])

/** Sending an approved result back to closed during its dispute window
 * ([3.3] #11): the reason popup's state, the call, and what to show for each
 * documented error. `onSentBack` fires once the send-back succeeds, so the
 * caller can repaint without waiting for a refetch (market-service.md's
 * dispute-screen notes, point 6). */
export function useDisputeMarket(marketId: string, proposalId: string | null, onSentBack: () => void) {
  const [showDisputeModal, setShowDisputeModal] = useState(false)

  const {
    reason, setReason, submitting, formError, reasonError, superseded, canConfirm, handleConfirm: handleDispute, resetReason,
  } = useReasonAction({
    submit: reason => sendBackOutcome(marketId, proposalId, reason),
    incompleteCode: 'send_back_incomplete',
    codeMessages: CODE_MESSAGES,
    supersedingCodes: SUPERSEDING_CODES,
    genericFailureMessage: 'Sending this result back failed. Please try again.',
    onDone: () => { setShowDisputeModal(false); resetReason(); onSentBack() },
    onError: () => setShowDisputeModal(false),
  })

  return {
    showDisputeModal,
    openDisputeModal: () => setShowDisputeModal(true),
    closeDisputeModal: () => setShowDisputeModal(false),
    reason,
    setReason,
    submitting,
    formError,
    reasonError,
    superseded,
    canConfirm,
    handleDispute,
  }
}
