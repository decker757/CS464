import { useState } from 'react'
import { sendBackOutcome } from '../api/marketApi'
import { errorCode, errorDetails } from '../api/errors'

const MIN_REASON_LENGTH = 10

// What the admin should do for each documented 409
// (market-service.md, send-back-outcome's "When it is refused").
const CODE_MESSAGES: Record<string, string> = {
  dispute_window_closed: 'Too late. The dispute window has closed.',
  proposal_superseded: 'This result was already replaced.',
  market_not_approved: 'Somebody else acted on this market first.',
  market_already_settled: 'Somebody else acted on this market first.',
  market_pending_resolution: 'Somebody else acted on this market first.',
}

// All five documented 409s mean a retry cannot succeed without a fresh read
// of the row — there is no "reason too short" case to recover from in place.
const SUPERSEDING_CODES = new Set([
  'dispute_window_closed', 'proposal_superseded',
  'market_not_approved', 'market_already_settled', 'market_pending_resolution',
])

/** Sending an approved result back to closed during its dispute window
 * ([3.3] #11): the reason popup's state, the call, and what to show for each
 * documented error. `onSentBack` fires once the send-back succeeds, so the
 * caller can repaint without waiting for a refetch (market-service.md's
 * dispute-screen notes, point 6). */
export function useDisputeMarket(marketId: string, proposalId: string | null, onSentBack: () => void) {
  const [showDisputeModal, setShowDisputeModal] = useState(false)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [reasonError, setReasonError] = useState('')
  // Another administrator acted on this market, or the window closed, while
  // this row was on screen — the control stays disabled rather than let a
  // retry hit the same wall (market-service.md's dispute-screen notes, point 7).
  const [superseded, setSuperseded] = useState(false)

  const canConfirm = reason.trim().length >= MIN_REASON_LENGTH

  const handleDispute = async () => {
    if (submitting) return
    setSubmitting(true)
    setFormError('')
    setReasonError('')
    try {
      await sendBackOutcome(marketId, proposalId, reason.trim())
      setShowDisputeModal(false)
      setReason('')
      onSentBack()
    } catch (err) {
      const code = errorCode(err)
      if (code === 'send_back_incomplete') {
        setReasonError(errorDetails(err).find(d => d.field === 'reason')?.message ?? 'Fix the reason and try again.')
        return
      }
      setFormError((code && CODE_MESSAGES[code]) || 'Sending this result back failed. Please try again.')
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
      setShowDisputeModal(false)
    } finally {
      setSubmitting(false)
    }
  }

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
