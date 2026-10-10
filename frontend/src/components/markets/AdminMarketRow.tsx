import { useState } from 'react'
import { Link } from 'react-router-dom'
import { closeMarket, type MarketOverviewRow } from '../../api/marketApi'
import { errorCode, errorDetails } from '../../api/errors'
import Button from '../ui/Button'
import Card from '../ui/Card'
import Field from '../ui/Field'
import TextArea from '../ui/TextArea'
import { buttonClass } from '../ui/buttonClass'
import StatusBadge from './StatusBadge'
import { formatCloseTime, tradingStopped } from './marketStatus'

const MIN_REASON_LENGTH = 10

interface AdminMarketRowProps {
  market: MarketOverviewRow
  isMine: boolean
}

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

// One market on the admin markets list ([2.1] #5). Only its creator can act on
// it, e.g. propose its outcome. Closing it early ([2.3] #7) is the one control
// any administrator may use on anybody's market (market-service.md's notes
// for #56, point 7), so it is driven from this row rather than the creator check.
export default function AdminMarketRow({ market, isMine }: AdminMarketRowProps) {
  // Set once the close succeeds, so the row repaints without waiting for the
  // list's next fetch (market-service.md's notes for #56, point 5).
  const [closedJustNow, setClosedJustNow] = useState(false)
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

  const status = closedJustNow ? 'closed' : market.status
  // Same derivation as everywhere else on this page: a market whose countdown
  // has hit zero has already closed, and cannot be closed again (market-service.md's
  // notes for #56, point 1).
  const canClose = !tradingStopped(status, market.close_time ?? '')
  const canConfirmClose = reason.trim().length >= MIN_REASON_LENGTH

  const handleClose = async () => {
    if (closing) return
    setClosing(true)
    setFormError('')
    setReasonError('')
    try {
      await closeMarket(market.id, reason.trim())
      setClosedJustNow(true)
      setShowCloseModal(false)
    } catch (err) {
      const code = errorCode(err)
      if (code === 'close_incomplete') {
        setReasonError(errorDetails(err).find(d => d.field === 'reason')?.message ?? 'Fix the reason and try again.')
        return
      }
      setFormError((code && CODE_MESSAGES[code]) || 'Closing this market failed. Please try again.')
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
    } finally {
      setClosing(false)
    }
  }

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
        {status !== 'draft' && status !== 'submitted' && (
          <Link to={`/admin/markets/${market.id}/price-history`} className={buttonClass('outline', 'xs')}>
            Price History
          </Link>
        )}
        {canClose && !superseded && (
          <Button variant="outline" size="xs" onClick={() => setShowCloseModal(true)}>
            Close
          </Button>
        )}
        <StatusBadge status={status} />
      </div>

      {showCloseModal && (
        <div role="dialog" aria-modal="true" aria-label="Close market" className="fixed inset-0 flex items-center justify-center bg-smu-navy/40 px-4">
          <div className="w-full max-w-[480px] rounded-2xl border border-smu-gold/15 bg-white p-6 shadow-card">
            <h2 className="mb-1 text-[17px] font-bold text-smu-navy">Close this market early?</h2>
            <p className="mb-4 text-xs text-subtle">
              Trading stops immediately and cannot be undone. The reason goes into the admin log and cannot be edited afterwards.
            </p>
            <Field id={`close-reason-${market.id}`} label="Reason *" error={reasonError}>
              <TextArea
                id={`close-reason-${market.id}`}
                invalid={!!reasonError}
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="Explain why this market is being closed early."
              />
            </Field>
            <div className="mt-4 flex justify-end gap-3">
              <Button variant="outline" size="sm" onClick={() => setShowCloseModal(false)} disabled={closing}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" onClick={handleClose} disabled={!canConfirmClose || closing}>
                {closing ? 'Closing…' : 'Confirm Close'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
