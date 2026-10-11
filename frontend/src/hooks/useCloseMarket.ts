import { useState } from 'react'
import { closeMarket } from '../api/marketApi'
import { errorCode, errorDetails } from '../api/errors'

const MIN_REASON_LENGTH = 10

// What the admin should do for each documented 409 (market-service.md,
// "Only an open market"). A refused close writes nothing, so there is no
// state to roll back — just tell the admin and let them reload.
const CODE_MESSAGES: Record<string, string> = {
  market_not_open: 'This market is not published yet, so it cannot be closed.',
  market_closed: 'Somebody else closed this market, or its closing time passed. Reload to see its current state.',
  market_pending_resolution: 'This market has already stopped and has a proposed outcome.',
  market_already_approved: 'This market has already stopped and its outcome is approved.',
}

// These three mean the market already stopped some other way — reload rather
// than retry (market-service.md's notes for #56, point 6).
const SUPERSEDING_CODES = new Set(['market_closed', 'market_pending_resolution', 'market_already_approved'])

/** Closing a market early ([2.3] #7): the reason popup's state, the close
 * call, and what to show for each documented error. `onClosed` fires once
 * the close succeeds, so the caller can repaint without waiting for the
 * list's next fetch (market-service.md's notes for #56, point 5). */
export function useCloseMarket(marketId: string, onClosed: () => void) {
  const [showCloseModal, setShowCloseModal] = useState(false)
  const [reason, setReason] = useState('')
  const [closing, setClosing] = useState(false)
  const [formError, setFormError] = useState('')
  // 422 close_incomplete's one entry, painted on the field it names
  // (market-service.md's notes for #56, point 3 — same shape as blocking_submission).
  const [reasonError, setReasonError] = useState('')
  // Another admin closed it, or the clock did, while this row was on screen —
  // the control stays disabled rather than let a retry hit the same wall.
  const [superseded, setSuperseded] = useState(false)

  const canConfirmClose = reason.trim().length >= MIN_REASON_LENGTH

  const handleClose = async () => {
    if (closing) return
    setClosing(true)
    setFormError('')
    setReasonError('')
    try {
      await closeMarket(marketId, reason.trim())
      setShowCloseModal(false)
      onClosed()
    } catch (err) {
      const code = errorCode(err)
      if (code === 'close_incomplete') {
        setReasonError(errorDetails(err).find(d => d.field === 'reason')?.message ?? 'Fix the reason and try again.')
        return
      }
      setFormError((code && CODE_MESSAGES[code]) || 'Closing this market failed. Please try again.')
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
      setShowCloseModal(false)
    } finally {
      setClosing(false)
    }
  }

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
