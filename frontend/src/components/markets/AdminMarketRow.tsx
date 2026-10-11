import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { MarketOverviewRow } from '../../api/marketApi'
import Button from '../ui/Button'
import Card from '../ui/Card'
import ReasonModal from '../ui/ReasonModal'
import { buttonClass } from '../ui/buttonClass'
import { useCloseMarket } from '../../hooks/useCloseMarket'
import StatusBadge from './StatusBadge'
import { formatCloseTime, tradingStopped } from './marketStatus'

interface AdminMarketRowProps {
  market: MarketOverviewRow
  isMine: boolean
}

// One market on the admin markets list ([2.1] #5). Only its creator can act on
// it, e.g. propose its outcome. Closing it early ([2.3] #7) is the one control
// any administrator may use on anybody's market (market-service.md's notes
// for #56, point 7), so it is driven from this row rather than the creator check.
export default function AdminMarketRow({ market, isMine }: AdminMarketRowProps) {
  // Set once the close succeeds, so the row repaints without waiting for the
  // list's next fetch (market-service.md's notes for #56, point 5).
  const [closedJustNow, setClosedJustNow] = useState(false)
  const {
    showCloseModal, openCloseModal, closeCloseModal,
    reason, setReason, closing, formError, reasonError, superseded,
    canConfirmClose, handleClose,
  } = useCloseMarket(market.id, () => setClosedJustNow(true))

  const status = closedJustNow ? 'closed' : market.status
  // Same derivation as everywhere else on this page: a market whose countdown
  // has hit zero has already closed, and cannot be closed again (market-service.md's
  // notes for #56, point 1).
  const canClose = !tradingStopped(status, market.close_time ?? '')

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
        {formError && (
          <div role="alert" className="mt-1 flex items-center gap-2 text-xs text-danger">
            <span>{formError}</span>
            {superseded && (
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
        {canClose && !superseded && (
          <Button variant="outline" size="xs" onClick={openCloseModal}>
            Close
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
    </Card>
  )
}
