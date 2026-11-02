import { Link } from 'react-router-dom'
import type { AdminAction } from '../../api/auditApi'
import type { MarketOverviewRow } from '../../api/marketApi'
import Card from '../ui/Card'
import { buttonClass } from '../ui/buttonClass'
import { useCloseMarket } from '../../hooks/useCloseMarket'
import { useDisputeMarket } from '../../hooks/useDisputeMarket'
import { usePublishMarket } from '../../hooks/usePublishMarket'
import { useSettleMarket } from '../../hooks/useSettleMarket'
import { useStatusAfterActions } from '../../hooks/useStatusAfterActions'
import { useVoidMarket } from '../../hooks/useVoidMarket'
import { useWindowOpen } from '../../hooks/useWindowOpen'
import { disputeWindowLine } from '../../utils/formatCountdown'
import { toApprovedResult } from '../../utils/proposals'
import CloseMarketControl from './CloseMarketControl'
import DisputeResultControl from './DisputeResultControl'
import PublishMarketControl from './PublishMarketControl'
import RowError from './RowError'
import SettleMarketControl from './SettleMarketControl'
import StatusBadge from './StatusBadge'
import VoidMarketControl from './VoidMarketControl'
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

function reloadPage() {
  window.location.reload()
}

// One market on the admin markets list ([2.1] #5). Only its creator can act on
// it, e.g. propose its outcome, resume its draft or publish it. Closing it
// early ([2.3] #7), settling ([3.4] #12) and voiding (#526) are controls any
// administrator may use on anybody's market (market-service.md's notes for
// #56, point 7), so they are driven from this row rather than the creator
// check. The ledger refuses a void from an administrator who traded it.
//
// Each action's hook lives here rather than in its control, because its error
// shows in this column, under the question, while its button and modal are in
// the control beside the others.
export default function AdminMarketRow({ market, isMine, approvalEntry }: AdminMarketRowProps) {
  const { status, markClosed, markSettled, markDisputed, markPublished, markVoided } = useStatusAfterActions(market)
  const closeAction = useCloseMarket(market.id, markClosed)
  const settleAction = useSettleMarket(market.id, markSettled)
  const publishAction = usePublishMarket(market.id, markPublished)

  // Only present on an approved row (AdminMarketsPage only looks this up for
  // those), so it also doubles as "is there anything to dispute here."
  const approvedResult = approvalEntry ? toApprovedResult({ id: market.id, question: market.question ?? '' }, approvalEntry) : null
  const disputeAction = useDisputeMarket(market.id, approvedResult?.proposalId ?? null, markDisputed)
  const voidAction = useVoidMarket(market.id, markVoided)

  // Same derivation as everywhere else on this page: a market whose countdown
  // has hit zero has already closed, and cannot be closed again (market-service.md's
  // notes for #56, point 1).
  const canClose = !tradingStopped(status, market.close_time)
  // A draft or a submitted market has no book and no trades, so neither the
  // exposure panel nor the price history has anything to show yet.
  const isPublished = market.status !== 'draft' && market.status !== 'submitted'
  // The countdown is display only; the server still decides on its own clock
  // (market-service.md's dispute-screen notes, point 8), so a window that
  // reads as still open here can still be refused as dispute_window_closed.
  // useWindowOpen re-evaluates the instant the window closes, so this and the
  // "Result is final" text do not go stale until something else re-renders —
  // which, on an approved market with no trades, could otherwise be hours.
  // Called unconditionally, as every hook must be; status gates the result.
  const disputeWindowStillOpen = useWindowOpen(market.dispute_window_ends_at)
  const windowOpen = status === 'approved' && disputeWindowStillOpen

  // Below md the controls go under the question rather than beside it: a
  // published market's three buttons and badge are too wide for a phone to
  // fit next to anything (#438).
  return (
    <Card className="flex flex-col gap-3 px-6 py-4 md:flex-row md:items-center md:justify-between md:gap-4">
      <div className="min-w-0">
        <p className="truncate text-[15px] font-semibold text-smu-navy">
          {market.question ?? <span className="italic text-subtle">Untitled market</span>}
        </p>
        <p className="mt-1 text-xs text-subtle">
          {formatCloseTime(status, market.close_time)}
          {isMine && ' · Created by you'}
        </p>
        {status === 'approved' && (
          <p className="mt-1 text-xs text-subtle">
            {disputeWindowLine(market.dispute_window_ends_at, windowOpen)}
          </p>
        )}
        {closeAction.formError && <RowError message={closeAction.formError} onReload={closeAction.superseded ? reloadPage : undefined} />}
        {settleAction.formError && (
          <RowError
            message={settleAction.formError}
            onReload={(settleAction.superseded || settleAction.needsReload) ? reloadPage : undefined}
          />
        )}
        {disputeAction.formError && <RowError message={disputeAction.formError} onReload={disputeAction.superseded ? reloadPage : undefined} />}
        {publishAction.formError && <RowError message={publishAction.formError} onReload={publishAction.superseded ? reloadPage : undefined} />}
        {voidAction.formError && <RowError message={voidAction.formError} onReload={voidAction.superseded ? reloadPage : undefined} />}
        {status === 'voided' && voidAction.refundsCut && (
          <p className="mt-1 text-xs text-subtle">The pool could not cover every refund, so each was cut by the same share.</p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3 md:shrink-0 md:flex-nowrap">
        {/* Both are the creator's: market_service scopes `GET /markets/{id}`
            and `publish` to the caller and answers 404 for anybody else's
            market, so offering either to another administrator would offer a
            control that cannot work. Neither appears past submitted — the
            form freezes there and publication is one way (ADR 0008). */}
        {isMine && status === 'draft' && (
          <Link to={`/admin/markets/${market.id}/edit`} className={buttonClass('outline', 'xs')}>
            Resume draft
          </Link>
        )}
        <PublishMarketControl canPublish={isMine && status === 'submitted'} publishAction={publishAction} />
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
        <CloseMarketControl marketId={market.id} canClose={canClose} closeAction={closeAction} />
        <SettleMarketControl canSettle={market.settleable && status !== 'settled'} settleAction={settleAction} />
        <DisputeResultControl marketId={market.id} windowOpen={windowOpen} approvedResult={approvedResult} disputeAction={disputeAction} />
        <VoidMarketControl marketId={market.id} voidable={market.voidable} isVoided={status === 'voided'} voidAction={voidAction} />
        <StatusBadge status={status} />
      </div>
    </Card>
  )
}
