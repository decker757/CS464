import { useState } from 'react'
import { errorCode } from '../api/errors'
import { publishMarket } from '../api/marketApi'

// What the admin should do for each documented code (market-service.md,
// POST /markets/{id}/publish). A refused publish writes nothing, so there is
// no state to roll back — tell them what happened and let them reload.
const CODE_MESSAGES: Record<string, string> = {
  market_not_found: 'This market is not available.',
  market_not_submitted: 'This market is not waiting for review any more. Reload to see its current state.',
  market_already_open: 'This market is already live for traders. Reload to see its current state.',
  market_closed: 'This market has already been published, and it has closed. Reload to see its current state.',
  market_pending_resolution: 'This market already has an outcome waiting for a second administrator. Reload to see its current state.',
  market_already_approved: 'This market already has an approved outcome. Reload to see its current state.',
  market_already_settled: 'This market has already been settled. Reload to see its current state.',
  // Publishing re-runs every submission rule against the clock now (ADR
  // 0008), so a market submitted last week can fail on a close time that has
  // since passed. No screen can edit a submitted market (#354), so neither a
  // retry nor a reload is the advice.
  draft_incomplete:
    'These terms no longer pass the checks for publishing, and a submitted market cannot be edited. '
    + 'Create a new market with the corrections instead.',
}

// These mean the market is not submitted any more, so no retry can succeed
// without a fresh read of the row.
const SUPERSEDING_CODES = new Set([
  'market_not_found',
  'market_not_submitted',
  'market_already_open',
  'market_closed',
  'market_pending_resolution',
  'market_already_approved',
  'market_already_settled',
])

/** Publishing a submitted market ([1.3] #3, #348): the confirm popup's state,
 * the publish call, and what to show for each documented error. `onPublished`
 * fires once it succeeds, so the caller can repaint without waiting for the
 * list's next fetch, the same as `useCloseMarket` and `useSettleMarket`.
 * Publication is one way (ADR 0008), which is why it is confirmed first. */
export function usePublishMarket(marketId: string, onPublished: () => void) {
  const [showPublishModal, setShowPublishModal] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [formError, setFormError] = useState('')
  const [superseded, setSuperseded] = useState(false)

  const handlePublish = async () => {
    if (publishing) return
    setPublishing(true)
    setFormError('')
    try {
      await publishMarket(marketId)
      setShowPublishModal(false)
      onPublished()
    } catch (err) {
      const code = errorCode(err)
      setFormError((code && CODE_MESSAGES[code]) || 'Publishing this market failed. Please try again.')
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
      // The message is painted on the page behind the modal's overlay, so a
      // modal left open hides it. Same reasoning as `useReasonAction`.
      setShowPublishModal(false)
    } finally {
      setPublishing(false)
    }
  }

  return {
    showPublishModal,
    openPublishModal: () => setShowPublishModal(true),
    closePublishModal: () => setShowPublishModal(false),
    publishing,
    formError,
    superseded,
    handlePublish,
  }
}
