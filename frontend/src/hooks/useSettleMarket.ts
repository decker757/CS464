import { useState } from 'react'
import { settleMarket } from '../api/ledgerApi'
import { errorCode } from '../api/errors'

// What the admin should do for each documented code
// (ledger-service.md, POST /ledger/markets/{id}/settlement).
const CODE_MESSAGES: Record<string, string> = {
  market_not_approved: 'This market is no longer approved. Reload to see its current state.',
  dispute_window_open: 'The dispute window has not closed yet. Nothing was paid.',
  market_terms_unavailable: 'The market service could not be reached. Nothing was paid; try again.',
}

// These mean a retry cannot succeed without a fresh read of the row.
const SUPERSEDING_CODES = new Set(['market_not_approved'])
// These mean the row's own data is stale and needs a reload, but Settle
// should stay available — the window's edge passes in seconds.
const RELOAD_CODES = new Set(['dispute_window_open'])

/** Settling an approved market ([3.4] #12): the settlement call and what to
 * show for each documented error. `settlement_unconfirmed` means the payouts
 * already committed and only the last step (telling market_service) failed —
 * ledger-service.md is explicit that a client must not read this as failure,
 * so it retries on its own rather than asking the admin to click again, and
 * `onSettled` still fires once that retry confirms it. Once that code has
 * been seen, the money has moved: the retry's own error must never be read
 * through CODE_MESSAGES, which is written for a first attempt where nothing
 * has been paid yet (ledger-service.md: "do not tell the administrator that
 * the settlement failed"). */
export function useSettleMarket(marketId: string, onSettled: () => void) {
  const [showSettleModal, setShowSettleModal] = useState(false)
  const [settling, setSettling] = useState(false)
  const [formError, setFormError] = useState('')
  const [superseded, setSuperseded] = useState(false)
  const [needsReload, setNeedsReload] = useState(false)

  const attempt = async (): Promise<void> => {
    await settleMarket(marketId)
    onSettled()
  }

  const handleSettle = async () => {
    if (settling) return
    setSettling(true)
    setFormError('')
    try {
      await attempt()
      setShowSettleModal(false)
    } catch (err) {
      const code = errorCode(err)
      if (code === 'settlement_unconfirmed') {
        try {
          await attempt()
          setShowSettleModal(false)
          return
        } catch {
          setFormError('Settlement is recorded, but confirming it with the market failed. Try again.')
          setShowSettleModal(false)
          return
        }
      }
      setFormError((code && CODE_MESSAGES[code]) || 'Settling this market failed. Please try again.')
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
      setNeedsReload(!!code && RELOAD_CODES.has(code))
      setShowSettleModal(false)
    } finally {
      setSettling(false)
    }
  }

  return {
    showSettleModal,
    openSettleModal: () => setShowSettleModal(true),
    closeSettleModal: () => setShowSettleModal(false),
    settling,
    formError,
    superseded,
    needsReload,
    handleSettle,
  }
}
