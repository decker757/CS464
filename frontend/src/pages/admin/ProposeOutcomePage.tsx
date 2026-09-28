import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { describeFormError, errorCode } from '../../api/errors'
import { getMyMarket, proposeOutcome, type MarketOut, type OutcomeOut } from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import BackLink from '../../components/ui/BackLink'
import Button from '../../components/ui/Button'
import Field from '../../components/ui/Field'
import PageTitle from '../../components/ui/PageTitle'
import SectionCard from '../../components/ui/SectionCard'
import TextArea from '../../components/ui/TextArea'
import TextInput from '../../components/ui/TextInput'

const FIELDS = ['winning_outcome_id', 'evidence_url', 'evidence_note', 'evidence'] as const

// Why this market's own status blocks the page from loading the form at all —
// only "closed" ever reaches the form itself.
const NOT_CLOSED_MESSAGES: Record<Exclude<MarketOut['status'], 'closed'>, string> = {
  draft: 'This market is still a draft and has not been published.',
  submitted: 'This market has been submitted but not published yet.',
  open: 'This market has not finished yet. Wait for it to close before proposing an outcome.',
  pending_resolution: 'An outcome has already been proposed for this market and is awaiting approval.',
  approved: 'An outcome has already been approved for this market.',
}

// What the admin should do, per error code, from market-service.md's
// "Only a closed market" table.
const CODE_MESSAGES: Record<string, string> = {
  market_not_found: 'This market is not available.',
  market_not_closed: NOT_CLOSED_MESSAGES.open,
  market_pending_resolution: NOT_CLOSED_MESSAGES.pending_resolution,
  market_already_approved: NOT_CLOSED_MESSAGES.approved,
}

// Somebody got there first. The page reloads the market and shows their
// proposal or approval instead of the form (market-service.md, notes for #52).
const DECIDED_ELSEWHERE_CODES = ['market_pending_resolution', 'market_already_approved']

// Says who proposed or approved, when the market says so.
function describeNotClosed(market: MarketOut): string {
  if (market.status === 'pending_resolution' && market.proposed_at) {
    const proposedAt = new Date(market.proposed_at).toLocaleString('en-SG')
    return `Awaiting approval: proposed by ${market.proposed_by_username} on ${proposedAt}.`
  }
  if (market.status === 'approved' && market.approved_at) {
    const approvedAt = new Date(market.approved_at).toLocaleString('en-SG')
    return `Approved by ${market.approved_by_username} on ${approvedAt}.`
  }
  if (market.status === 'closed') return ''
  return NOT_CLOSED_MESSAGES[market.status]
}

export default function ProposeOutcomePage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [market, setMarket] = useState<MarketOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)

  const [winningOutcomeId, setWinningOutcomeId] = useState('')
  const [evidenceUrl, setEvidenceUrl] = useState('')
  const [evidenceNote, setEvidenceNote] = useState('')

  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [formError, setFormError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!id) return
    getMyMarket(id)
      .then(setMarket)
      .catch(() => setLoadError(true))
      .finally(() => setLoading(false))
  }, [id])

  const handleSubmit = async () => {
    if (!id || submitting) return
    setSubmitting(true)
    setFieldErrors({})
    setFormError('')
    try {
      await proposeOutcome(id, {
        winning_outcome_id: winningOutcomeId,
        evidence_url: evidenceUrl.trim() || undefined,
        evidence_note: evidenceNote.trim() || undefined,
      })
      navigate('/admin/markets')
    } catch (err) {
      const code = errorCode(err)
      if (code && DECIDED_ELSEWHERE_CODES.includes(code)) {
        const freshMarket = await getMyMarket(id).catch(() => null)
        if (freshMarket) {
          setMarket(freshMarket)
          return
        }
      }
      const view = describeFormError(err, {
        fields: FIELDS,
        codeMessages: CODE_MESSAGES,
        fieldErrorCodes: ['proposal_incomplete'],
      })
      setFieldErrors(view.fieldErrors)
      setFormError(view.formError)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <AppLayout width="max-w-[700px]">
        <p className="text-sm text-muted">Loading market…</p>
      </AppLayout>
    )
  }

  if (loadError || !market) {
    return (
      <AppLayout width="max-w-[700px]">
        <p role="alert" className="text-sm text-danger">Failed to load this market. Please try again.</p>
      </AppLayout>
    )
  }

  if (market.status !== 'closed') {
    return (
      <AppLayout width="max-w-[700px]">
        <BackLink to="/admin/markets" label="My Markets" className="mb-4 block" />
        <p className="text-sm text-muted">{describeNotClosed(market)}</p>
      </AppLayout>
    )
  }

  const cannotSubmit = submitting || !winningOutcomeId || (!evidenceUrl.trim() && !evidenceNote.trim())

  return (
    <AppLayout width="max-w-[700px]">
      <BackLink to="/admin/markets" label="My Markets" className="mb-1.5 block" />
      <PageTitle className="mb-8">Propose Outcome</PageTitle>

      <SectionCard title={market.question ?? 'Untitled market'} className="mb-5">
        <p className="mb-2 text-[13px] font-semibold text-smu-navy">Winning outcome *</p>
        {fieldErrors.winning_outcome_id && (
          <p role="alert" className="mb-2 text-xs text-danger">{fieldErrors.winning_outcome_id}</p>
        )}
        <div className="flex flex-col gap-2">
          {market.outcomes.map((outcome: OutcomeOut) => (
            <button
              key={outcome.id}
              type="button"
              onClick={() => setWinningOutcomeId(outcome.id)}
              aria-pressed={winningOutcomeId === outcome.id}
              className={`cursor-pointer rounded-control border px-4 py-3 text-left text-[14px] font-medium transition ${
                winningOutcomeId === outcome.id
                  ? 'border-smu-navy bg-smu-navy text-white'
                  : 'border-smu-navy/20 text-smu-navy hover:border-smu-navy/40'
              }`}
            >
              {outcome.label}
            </button>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Evidence" className="mb-5">
        <p className="-mt-3 mb-4 text-xs text-subtle">Give a source URL, a written note, or both.</p>
        {fieldErrors.evidence && <p role="alert" className="-mt-2 mb-4 text-xs text-danger">{fieldErrors.evidence}</p>}
        <div className="flex flex-col gap-4">
          <Field id="evidence-url" label="Source URL" error={fieldErrors.evidence_url}>
            <TextInput
              id="evidence-url"
              invalid={!!fieldErrors.evidence_url}
              value={evidenceUrl}
              onChange={e => setEvidenceUrl(e.target.value)}
              placeholder="https://…"
            />
          </Field>
          <Field id="evidence-note" label="Written note" error={fieldErrors.evidence_note}>
            <TextArea
              id="evidence-note"
              invalid={!!fieldErrors.evidence_note}
              value={evidenceNote}
              onChange={e => setEvidenceNote(e.target.value)}
              placeholder="Explain how this was decided, so a second administrator and a disputer can check it."
            />
          </Field>
        </div>
      </SectionCard>

      <div className="flex flex-col items-start gap-3 pb-12">
        {formError && <p role="alert" className="text-[13px] text-danger">{formError}</p>}
        <Button variant="primary" size="lg" onClick={handleSubmit} disabled={cannotSubmit}>
          {submitting ? 'Submitting…' : 'Propose Outcome'}
        </Button>
      </div>
    </AppLayout>
  )
}
