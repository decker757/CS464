import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { listAllActions } from '../../api/auditApi'
import { errorCode } from '../../api/errors'
import { approveOutcome, getMarket, rejectOutcome, type PublicMarketDetail } from '../../api/marketApi'
import { useAuth } from '../../context/AuthContext'
import AppLayout from '../../components/layout/AppLayout'
import BackLink from '../../components/ui/BackLink'
import Button from '../../components/ui/Button'
import Field from '../../components/ui/Field'
import PageTitle from '../../components/ui/PageTitle'
import SectionCard from '../../components/ui/SectionCard'
import TextArea from '../../components/ui/TextArea'
import { latestProposalPerMarket, toPendingProposal, type PendingProposal } from '../../utils/proposals'

const MIN_REASON_LENGTH = 10

// What the admin should do for each documented 409 (market-service.md,
// "Only a pending proposal, and not your own" / reject's "When it is refused").
const CODE_MESSAGES: Record<string, string> = {
  market_not_pending_resolution: 'This proposal is no longer waiting on a decision. Reload to see its current state.',
  market_already_approved: 'This proposal has already been approved by another administrator.',
  proposal_superseded: 'This proposal was rejected and a new one made since you loaded this page. Reload to review it.',
}

// These three codes mean somebody else settled it — reload rather than retry.
const SUPERSEDING_CODES = new Set(['market_not_pending_resolution', 'market_already_approved', 'proposal_superseded'])

export default function DecideOutcomePage() {
  const { id } = useParams<{ id: string }>()
  const { user } = useAuth()
  const navigate = useNavigate()

  const [market, setMarket] = useState<PublicMarketDetail | null>(null)
  const [proposal, setProposal] = useState<PendingProposal | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)

  const [approving, setApproving] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [showRejectModal, setShowRejectModal] = useState(false)
  const [reason, setReason] = useState('')
  const [formError, setFormError] = useState('')
  // Another administrator settled this proposal while it was on screen — the
  // controls stay disabled rather than let a retry hit the same wall
  // (market-service.md's approval-screen notes, point 7).
  const [superseded, setSuperseded] = useState(false)

  useEffect(() => {
    if (!id) return
    Promise.all([getMarket(id), listAllActions('market.outcome_proposed')])
      .then(([marketDetail, entries]) => {
        setMarket(marketDetail)
        const latest = latestProposalPerMarket(entries.filter(e => e.target_id === id))
        const entry = latest.get(id)
        if (entry) setProposal(toPendingProposal(marketDetail, entry))
      })
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }, [id])

  const handleApprove = async () => {
    if (!id || !proposal || approving) return
    setApproving(true)
    setFormError('')
    try {
      await approveOutcome(id, proposal.proposalId)
      navigate('/admin/proposals')
    } catch (err) {
      const code = errorCode(err)
      setFormError((code && CODE_MESSAGES[code]) || 'Approval failed. Please try again.')
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
      setApproving(false)
    }
  }

  const handleReject = async () => {
    if (!id || !proposal || rejecting) return
    setRejecting(true)
    setFormError('')
    try {
      await rejectOutcome(id, proposal.proposalId, reason.trim())
      navigate('/admin/proposals')
    } catch (err) {
      const code = errorCode(err)
      setFormError((code && CODE_MESSAGES[code]) || 'Rejection failed. Please try again.')
      setSuperseded(!!code && SUPERSEDING_CODES.has(code))
      setRejecting(false)
      setShowRejectModal(false)
    }
  }

  if (loading) {
    return (
      <AppLayout width="max-w-[700px]">
        <p className="text-sm text-muted">Loading proposal…</p>
      </AppLayout>
    )
  }

  if (loadError || !market) {
    return (
      <AppLayout width="max-w-[700px]">
        <p role="alert" className="text-sm text-danger">Failed to load this proposal. Please try again.</p>
      </AppLayout>
    )
  }

  if (!proposal) {
    return (
      <AppLayout width="max-w-[700px]">
        <BackLink to="/admin/proposals" label="Proposals" className="mb-4 block" />
        <p className="text-sm text-muted">This market has no proposal waiting on a decision.</p>
      </AppLayout>
    )
  }

  const isOwnProposal = proposal.proposedById === user?.id
  const canDecideReason = reason.trim().length >= MIN_REASON_LENGTH

  return (
    <AppLayout width="max-w-[700px]">
      <BackLink to="/admin/proposals" label="Proposals" className="mb-1.5 block" />
      <PageTitle className="mb-8">Review Proposal</PageTitle>

      <SectionCard title={market.question} className="mb-5">
        <dl className="flex flex-col gap-3 text-[14px]">
          <div>
            <dt className="text-xs font-semibold text-subtle uppercase">Proposed winner</dt>
            <dd className="text-smu-navy">{proposal.winningOutcome}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold text-subtle uppercase">Proposed by</dt>
            <dd className="text-smu-navy">{proposal.proposedByUsername} on {new Date(proposal.proposedAt).toLocaleString('en-SG')}</dd>
          </div>
        </dl>
      </SectionCard>

      <SectionCard title="Evidence" className="mb-5">
        {proposal.evidenceUrl && (
          <p className="mb-2 text-[14px]">
            <a href={proposal.evidenceUrl} target="_blank" rel="noreferrer" className="text-info underline">
              {proposal.evidenceUrl}
            </a>
          </p>
        )}
        {proposal.evidenceNote && <p className="text-[14px] text-smu-navy">{proposal.evidenceNote}</p>}
        {!proposal.evidenceUrl && !proposal.evidenceNote && (
          <p className="text-[14px] text-subtle">No evidence was given.</p>
        )}
      </SectionCard>

      {isOwnProposal && (
        <p className="mb-4 text-[13px] text-subtle">
          You proposed this outcome; another administrator has to decide it.
        </p>
      )}

      {formError && (
        <div role="alert" className="mb-4 flex items-center gap-3 text-[13px] text-danger">
          <span>{formError}</span>
          {superseded && (
            <Button variant="outline" size="xs" onClick={() => window.location.reload()}>
              Reload
            </Button>
          )}
        </div>
      )}

      <div className="flex items-center gap-3 pb-12">
        <Button variant="primary" size="lg" onClick={handleApprove} disabled={isOwnProposal || approving || rejecting || superseded}>
          {approving ? 'Approving…' : 'Approve'}
        </Button>
        <Button variant="outline" size="lg" onClick={() => setShowRejectModal(true)} disabled={isOwnProposal || approving || rejecting || superseded}>
          Reject
        </Button>
      </div>

      {showRejectModal && (
        <div role="dialog" aria-modal="true" aria-label="Reject proposal" className="fixed inset-0 flex items-center justify-center bg-smu-navy/40 px-4">
          <div className="w-full max-w-[480px] rounded-2xl border border-smu-gold/15 bg-white p-6 shadow-card">
            <h2 className="mb-1 text-[17px] font-bold text-smu-navy">Reject this proposal?</h2>
            <p className="mb-4 text-xs text-subtle">
              This goes into the admin log and cannot be edited afterwards. The proposal will be cleared from the market.
            </p>
            <Field id="reject-reason" label="Reason *">
              <TextArea
                id="reject-reason"
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="Explain why this proposal is being rejected."
              />
            </Field>
            <div className="mt-4 flex justify-end gap-3">
              <Button variant="outline" size="sm" onClick={() => setShowRejectModal(false)} disabled={rejecting}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" onClick={handleReject} disabled={!canDecideReason || rejecting}>
                {rejecting ? 'Rejecting…' : 'Confirm Rejection'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </AppLayout>
  )
}
