import Button from '../ui/Button'
import ReasonModal from '../ui/ReasonModal'
import type { useDisputeMarket } from '../../hooks/useDisputeMarket'
import type { ApprovedResult } from '../../utils/proposals'

interface DisputeResultControlProps {
  marketId: string
  // An approved row whose dispute window the countdown still shows as open.
  windowOpen: boolean
  // Null until the row's approval entry has loaded, and on any row that is not
  // approved; there is nothing to dispute without it.
  approvedResult: ApprovedResult | null
  disputeAction: ReturnType<typeof useDisputeMarket>
}

// Send an approved result back ([FE][3.3] #59): the row's Dispute result
// button and the modal showing what was approved.
export default function DisputeResultControl({ marketId, windowOpen, approvedResult, disputeAction }: DisputeResultControlProps) {
  return (
    <>
      {windowOpen && approvedResult && !disputeAction.superseded && (
        <Button variant="outline" size="xs" onClick={disputeAction.openDisputeModal}>
          Dispute result
        </Button>
      )}
      {disputeAction.showDisputeModal && approvedResult && (
        <ReasonModal
          ariaLabel="Dispute result"
          title="Dispute this result?"
          description="The market goes back to closed. The outcome must be proposed and approved again. The reason goes into the admin log and cannot be edited afterwards."
          details={
            // market-service.md's dispute-screen notes, point 3: show what
            // was approved, so an admin disputing one of several approved
            // rows is not asked to overturn a result they cannot see.
            <dl className="mb-4 flex flex-col gap-2 text-[13px]">
              <div>
                <dt className="text-[11px] font-semibold text-subtle uppercase">Winner</dt>
                <dd className="text-smu-navy">{approvedResult.winningOutcome}</dd>
              </div>
              {(approvedResult.evidenceUrl || approvedResult.evidenceNote) && (
                <div>
                  <dt className="text-[11px] font-semibold text-subtle uppercase">Evidence</dt>
                  {approvedResult.evidenceUrl && (
                    <dd>
                      <a href={approvedResult.evidenceUrl} target="_blank" rel="noreferrer" className="text-info underline">
                        {approvedResult.evidenceUrl}
                      </a>
                    </dd>
                  )}
                  {approvedResult.evidenceNote && <dd className="text-smu-navy">{approvedResult.evidenceNote}</dd>}
                </div>
              )}
              <div>
                <dt className="text-[11px] font-semibold text-subtle uppercase">Proposed by</dt>
                <dd className="text-smu-navy">{approvedResult.proposedByUsername}</dd>
              </div>
              <div>
                <dt className="text-[11px] font-semibold text-subtle uppercase">Approved by</dt>
                <dd className="text-smu-navy">{approvedResult.approvedByUsername}</dd>
              </div>
            </dl>
          }
          fieldId={`dispute-reason-${marketId}`}
          reason={disputeAction.reason}
          onReasonChange={disputeAction.setReason}
          reasonError={disputeAction.reasonError}
          placeholder="Explain why this approved result is being disputed."
          canConfirm={disputeAction.canConfirm}
          isSubmitting={disputeAction.submitting}
          confirmLabel="Confirm Dispute"
          confirmingLabel="Disputing…"
          onCancel={disputeAction.closeDisputeModal}
          onConfirm={disputeAction.handleDispute}
        />
      )}
    </>
  )
}
