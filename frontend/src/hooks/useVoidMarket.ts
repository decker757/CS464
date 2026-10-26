import { useState } from 'react'
import { voidMarket } from '../api/ledgerApi'
import { useReasonAction } from './useReasonAction'

// What `void_unconfirmed` means: every holder has been refunded and the void
// is recorded, and only telling market_service failed. It is never a failure
// (ledger-service.md: "Do not tell the administrator that the void failed").
const REFUNDS_RECORDED = 'The refunds went through, but the market service has not recorded the void yet. Press Finish to complete it.'
const MARKET_SERVICE_UNREACHABLE = 'The market service could not be reached; try again.'

// What the admin is told for each documented refusal of a first attempt
// (market-service.md's void-screen notes, point 4).
const CODE_MESSAGES: Record<string, string> = {
  admin_has_a_stake: 'You traded in this market, so another admin has to void it.',
  market_not_voidable: 'This market can no longer be voided.',
  market_already_settled: 'This market was settled first.',
  market_terms_unavailable: MARKET_SERVICE_UNREACHABLE,
  void_replay_mismatch: "This market's records do not add up, so it cannot be voided. Nothing was refunded.",
  void_unconfirmed: REFUNDS_RECORDED,
}

// Once the refunds are recorded, a repeat runs neither the stake check nor
// the consistency check and is never refused for the market's status
// (ledger-service.md, "A repeat writes nothing here"). So these are the only
// answers a Finish can get, and nothing written for a first attempt — where
// "Nothing was refunded" can be true — is read for it.
const FINISH_MESSAGES: Record<string, string> = {
  void_unconfirmed: REFUNDS_RECORDED,
  market_terms_unavailable: MARKET_SERVICE_UNREACHABLE,
}
const FINISH_FALLBACK = 'The refunds went through, but finishing the void did not complete. Press Finish to try again.'

// The market changed under the row: reload rather than retry.
const SUPERSEDING_CODES = new Set(['market_not_voidable', 'market_already_settled'])
// A retry from this screen cannot help, and a reload would offer the same
// control again: another administrator has to void it, or a developer has to
// look at its records.
const WITHDRAWING_CODES = new Set(['admin_has_a_stake', 'void_replay_mismatch'])

/** Voiding a flawed market ([BE] #221, #526): the reason popup's state, the
 * ledger's void call, and what to show for each documented answer.
 *
 * `void_unconfirmed` is the one answer that leaves work for this screen: the
 * money has moved, so the control becomes Finish, which repeats the same
 * request with the same reason. It is offered only in the session that got
 * the 503 (market-service.md's void-screen notes, point 5), and unlike
 * settlement's retry it waits for the admin to press it. `onVoided` fires
 * once a request answers 200, so the row can repaint without a refetch. */
export function useVoidMarket(marketId: string, onVoided: () => void) {
  const [showVoidModal, setShowVoidModal] = useState(false)
  const [awaitingFinish, setAwaitingFinish] = useState(false)
  const [withdrawn, setWithdrawn] = useState(false)
  const [refundsCut, setRefundsCut] = useState(false)

  const {
    reason, setReason, submitting, formError, reasonError, superseded, canConfirm, handleConfirm: handleVoid,
  } = useReasonAction({
    submit: reason => voidMarket(marketId, reason),
    incompleteCode: 'void_incomplete',
    codeMessages: awaitingFinish ? FINISH_MESSAGES : CODE_MESSAGES,
    supersedingCodes: awaitingFinish ? undefined : SUPERSEDING_CODES,
    genericFailureMessage: awaitingFinish ? FINISH_FALLBACK : 'Voiding this market failed. Please try again.',
    onDone: result => {
      setShowVoidModal(false)
      setAwaitingFinish(false)
      setRefundsCut(result.refunds_cut)
      onVoided()
    },
    onError: code => {
      setShowVoidModal(false)
      if (code === 'void_unconfirmed') setAwaitingFinish(true)
      if (!awaitingFinish && code && WITHDRAWING_CODES.has(code)) setWithdrawn(true)
    },
  })

  return {
    showVoidModal,
    openVoidModal: () => setShowVoidModal(true),
    closeVoidModal: () => setShowVoidModal(false),
    reason,
    setReason,
    submitting,
    formError,
    reasonError,
    superseded,
    withdrawn,
    awaitingFinish,
    refundsCut,
    canConfirm,
    handleVoid,
  }
}
