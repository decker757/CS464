import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { AdminAction } from '../../api/auditApi'
import type { MarketOverviewRow } from '../../api/marketApi'
import Button from '../ui/Button'
import Card from '../ui/Card'
import ConfirmModal from '../ui/ConfirmModal'
import ReasonModal from '../ui/ReasonModal'
import { buttonClass } from '../ui/buttonClass'
import { useCloseMarket } from '../../hooks/useCloseMarket'
import { useDisputeMarket } from '../../hooks/useDisputeMarket'
import { useSettleMarket } from '../../hooks/useSettleMarket'
import { disputeWindowOpen, formatCountdown } from '../../utils/formatCountdown'
import { toApprovedResult } from '../../utils/proposals'
import StatusBadge from './StatusBadge'
import { formatCloseTime, tradingStopped } from './marketStatus'

interface AdminMarketRowProps {
  market: MarketOverviewRow
  isMine: boolean
  // The latest market.outcome_approved entry for this market, for the
  // Dispute modal's content and the proposal_id it must quote ([FE][3.3]
  // #59). Undefined on any row that is not approved, or while it is still
  // loading (AdminMarketsPage fetches it once for the whole page).
  approvalEntry?: AdminAction
}

function RowError({ message, onReload }: { message: string; onReload?: () => void }) {
  return (
    <div role="alert" className="mt-1 flex items-center gap-2 text-xs text-danger">
      <span>{message}</span>
      {onReload && (
        <Button variant="outline" size="xs" onClick={onReload}>
          Reload
        </Button>
      )}
    </div>
  )
}

interface JustNow {
  disputedJustNow: boolean
  settledJustNow: boolean
  closedJustNow: boolean
}

// A send-back returns the market to closed, which beats everything else; after
// that settled wins over closed because a market is closed before it is settled.
function statusAfter(market: MarketOverviewRow, { disputedJustNow, settledJustNow, closedJustNow }: JustNow) {
  if (disputedJustNow) return 'closed'
  if (settledJustNow) return 'settled'
  if (closedJustNow) return 'closed'
  return market.status
}

// One market on the admin markets list ([2.1] #5). Only its creator can act on
// it, e.g. propose its outcome. Closing it early ([2.3] #7) and settling
// ([3.4] #12) are controls any administrator may use on anybody's market
// (market-service.md's notes for #56, point 7), so both are driven from this
// row rather than the creator check.
export default function AdminMarketRow({ market, isMine, approvalEntry }: AdminMarketRowProps) {
  // Set once the close succeeds, so the row repaints without waiting for the
  // list's next fetch (market-service.md's notes for #56, point 5).
  const [closedJustNow, setClosedJustNow] = useState(false)
  const [settledJustNow, setSettledJustNow] = useState(false)
  // Set once a send-back succeeds, same reasoning as closedJustNow/settledJustNow.
  const [disputedJustNow, setDisputedJustNow] = useState(false)
  const {
    showCloseModal, openCloseModal, closeCloseModal,
    reason, setReason, closing, formError, reasonError, superseded,
    canConfirmClose, handleClose,
  } = useCloseMarket(market.id, () => setClosedJustNow(true))
  const {
    showSettleModal, openSettleModal, closeSettleModal,
    settling, formError: settleError, superseded: settleSuperseded, needsReload: settleNeedsReload, handleSettle,
  } = useSettleMarket(market.id, () => setSettledJustNow(true))

  // Only present on an approved row (AdminMarketsPage only looks this up for
  // those), so it also doubles as "is there anything to dispute here."
  const approvedResult = approvalEntry ? toApprovedResult({ id: market.id, question: market.question ?? '' }, approvalEntry) : null
  const {
    showDisputeModal, openDisputeModal, closeDisputeModal,
    reason: disputeReason, setReason: setDisputeReason, submitting: disputing,
    formError: disputeError, reasonError: disputeReasonError, superseded: disputeSuperseded,
    canConfirm: canConfirmDispute, handleDispute,
  } = useDisputeMarket(market.id, approvedResult?.proposalId ?? null, () => setDisputedJustNow(true))

  const status = statusAfter(market, { disputedJustNow, settledJustNow, closedJustNow })
  // Same derivation as everywhere else on this page: a market whose countdown
  // has hit zero has already closed, and cannot be closed again (market-service.md's
  // notes for #56, point 1).
  const canClose = !tradingStopped(status, market.close_time ?? '')
  // A draft or a submitted market has no book and no trades, so neither the
  // exposure panel nor the price history has anything to show yet.
  const isPublished = market.status !== 'draft' && market.status !== 'submitted'
  // The countdown is display only; the server still decides on its own clock
  // (market-service.md's dispute-screen notes, point 8), so a window that
  // reads as still open here can still be refused as dispute_window_closed.
  const windowOpen = status === 'approved' && disputeWindowOpen(market.dispute_window_ends_at)

  return (
    <Card className="flex items-center justify-between gap-4 px-6 py-4">
      <div className="min-w-0">
        <p className="truncate text-[15px] font-semibold text-smu-navy">
          {market.question ?? <span className="italic text-subtle">Untitled market</span>}
        </p>
        <p className="mt-1 text-xs text-subtle">
          {formatCloseTime(status, market.close_time)}
          {isMine && ' · Created by you'}
        </p>
        {status === 'approved' && market.dispute_window_ends_at && (
          <p className="mt-1 text-xs text-subtle">
            {windowOpen ? `Disputes close in ${formatCountdown(market.dispute_window_ends_at)}` : 'Result is final; payout pending.'}
          </p>
        )}
        {formError && (
          <RowError message={formError} onReload={superseded ? () => window.location.reload() : undefined} />
        )}
        {settleError && (
          <RowError
            message={settleError}
            onReload={(settleSuperseded || settleNeedsReload) ? () => window.location.reload() : undefined}
          />
        )}
        {disputeError && (
          <div role="alert" className="mt-1 flex items-center gap-2 text-xs text-danger">
            <span>{disputeError}</span>
            {disputeSuperseded && (
              <Button variant="outline" size="xs" onClick={() => window.location.reload()}>
                Reload
              </Button>
            )}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {isMine && status === 'closed' && (
          <Link to={`/admin/markets/${market.id}/propose-outcome`} className={buttonClass('outline', 'xs')}>
            Propose Outcome
          </Link>
        )}
        {isPublished && (
          <>
            <Link to={`/admin/markets/${market.id}/exposure`} className={buttonClass('outline', 'xs')}>
              Exposure
            </Link>
            <Link to={`/admin/markets/${market.id}/price-history`} className={buttonClass('outline', 'xs')}>
              Price History
            </Link>
          </>
        )}
        {canClose && !superseded && (
          <Button variant="outline" size="xs" onClick={openCloseModal}>
            Close
          </Button>
        )}
        {market.settleable && status !== 'settled' && !settleSuperseded && (
          <Button variant="outline" size="xs" onClick={openSettleModal}>
            Settle
          </Button>
        )}
        {windowOpen && approvedResult && !disputeSuperseded && (
          <Button variant="outline" size="xs" onClick={openDisputeModal}>
            Dispute result
          </Button>
        )}
        <StatusBadge status={status} />
      </div>

      {showCloseModal && (
        <ReasonModal
          ariaLabel="Close market"
          title="Close this market early?"
          description="Trading stops immediately and cannot be undone. The reason goes into the admin log and cannot be edited afterwards."
          fieldId={`close-reason-${market.id}`}
          reason={reason}
          onReasonChange={setReason}
          reasonError={reasonError}
          placeholder="Explain why this market is being closed early."
          canConfirm={canConfirmClose}
          isSubmitting={closing}
          confirmLabel="Confirm Close"
          confirmingLabel="Closing…"
          onCancel={closeCloseModal}
          onConfirm={handleClose}
        />
      )}

      {showSettleModal && (
        <ConfirmModal
          ariaLabel="Settle market"
          title="Settle this market?"
          description="Every winning holder is paid out immediately. This cannot be undone."
          isSubmitting={settling}
          confirmLabel="Confirm Settle"
          confirmingLabel="Settling…"
          onCancel={closeSettleModal}
          onConfirm={handleSettle}
        />
      )}

      {showDisputeModal && approvedResult && (
        <ReasonModal
          ariaLabel="Dispute result"
          title="Dispute this result?"
          description="The market goes back to closed. The outcome must be proposed and approved again. The reason goes into the admin log and cannot be edited afterwards."
          fieldId={`dispute-reason-${market.id}`}
          reason={disputeReason}
          onReasonChange={setDisputeReason}
          reasonError={disputeReasonError}
          placeholder="Explain why this approved result is being disputed."
          canConfirm={canConfirmDispute}
          isSubmitting={disputing}
          confirmLabel="Confirm Dispute"
          confirmingLabel="Disputing…"
          onCancel={closeDisputeModal}
          onConfirm={handleDispute}
        />
      )}
    </Card>
  )
}
