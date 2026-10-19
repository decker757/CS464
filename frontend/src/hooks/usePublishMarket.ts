import { useState } from 'react'
import { errorCode, errorDetails } from '../api/errors'
import { publishMarket, type BlockingHint } from '../api/marketApi'

// What the admin should do for each code a publish can answer with
// (market-service.md, POST /markets/{id}/publish, "When it is refused").
// `market_pending_resolution` and `market_already_approved` are not in that
// table and reach here anyway: `_refuse_if_frozen` runs before the SUBMITTED
// check, so a market whose outcome is already proposed or approved answers
// with one of those rather than `market_closed`.
const CODE_MESSAGES: Record<string, string> = {
  market_not_found: 'This market is not available.',
  market_not_submitted: 'This market is still a draft, so there is nothing to publish yet. Submit it for review first.',
  market_already_open: 'This market is already live for traders. Reload to see its current state.',
  market_closed: 'This market has already been published, and it has closed. Reload to see its current state.',
  market_pending_resolution: 'This market already has an outcome waiting for a second administrator. Reload to see its current state.',
  market_already_approved: 'This market already has an approved outcome. Reload to see its current state.',
  market_already_settled: 'This market has already been settled. Reload to see its current state.',
  // Publishing re-runs every submission rule against the clock now (ADR
  // 0008), so a market submitted last week fails on a close time that has
  // since passed. Nothing can edit a market once it has been submitted
  // (#354), so neither a retry nor a reload is the advice; `refusalDetails`
  // says which fields, for a caller that has them on screen.
  draft_incomplete:
    'These terms no longer pass the checks for publishing, and a market cannot be edited '
    + 'once it has been submitted. Create a new market with the corrections instead.',
}

const GENERIC_FAILURE = 'Publishing this market failed. Please try again.'

// These mean the market has moved past submitted, so no retry can succeed
// without a fresh read of it. `market_not_submitted` is deliberately absent:
// it means the market has not got there yet, and what that needs is a
// submission, not a reload (market-service.md's table).
const SUPERSEDING_CODES = new Set([
  'market_not_found',
  'market_already_open',
  'market_closed',
  'market_pending_resolution',
  'market_already_approved',
  'market_already_settled',
])

/** Publishing a submitted market ([1.3] #3, #348): the confirm popup's state,
 * the publish call, and what to show for each documented error. The one place
 * that knows what a refused publish means, for both screens that offer it —
 * the markets list's row and the create form after a reload, which #348 made
 * a screen that can sit open indefinitely and so meet every stale-state code
 * in that table.
 *
 * `onPublished` fires once it succeeds, so a caller can repaint without
 * waiting for a refetch, the same as `useCloseMarket` and `useSettleMarket`.
 * `marketId` may be null while a caller has not got one yet; publishing is
 * then refused here rather than guarded at each call site. Publication is one
 * way (ADR 0008), which is why the row confirms it first. */
export function usePublishMarket(marketId: string | null, onPublished: () => void) {
  const [showPublishModal, setShowPublishModal] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [formError, setFormError] = useState('')
  const [superseded, setSuperseded] = useState(false)
  // Which fields a refusal was about, in the same shape a refused submission
  // carries (ADR 0008). Only the form has those fields on screen.
  const [refusalDetails, setRefusalDetails] = useState<BlockingHint[]>([])

  const handlePublish = async () => {
    if (!marketId || publishing) return
    setPublishing(true)
    setFormError('')
    setRefusalDetails([])
    try {
      await publishMarket(marketId)
      setShowPublishModal(false)
      onPublished()
    } catch (err) {
      const code = errorCode(err)
      setFormError((code && CODE_MESSAGES[code]) || GENERIC_FAILURE)
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
      setRefusalDetails(errorDetails(err))
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
    refusalDetails,
    handlePublish,
  }
}
