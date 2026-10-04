import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorCode, errorDetails } from '../../api/errors'
import {
  type BlockingHint,
  type SaveMarketResponse,
  publishMarket,
  saveMarket,
} from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import BackLink from '../../components/ui/BackLink'
import Badge from '../../components/ui/Badge'
import Button from '../../components/ui/Button'
import SectionCard from '../../components/ui/SectionCard'
import Field from '../../components/ui/Field'
import PageTitle from '../../components/ui/PageTitle'
import TextArea from '../../components/ui/TextArea'
import TextInput from '../../components/ui/TextInput'

type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

type OutcomeRow = { id: string; label: string }
type SourceRow = { id: string; url: string; label: string }

type FormState = {
  question: string
  description: string
  outcomes: OutcomeRow[]
  closeTime: string
  resolutionTime: string
  criteria: string
  sources: SourceRow[]
  liquidityB: string
  seedSubsidy: string
}

// The column holds at most 99999999999999.9999, which is 1e14 as a JS number.
const PRICE_LIMIT = 1e14

const DEFAULT_OUTCOME_PLACEHOLDERS = ['Yes', 'No']

// A save refused with one of these codes means the market already has this status.
const STATUS_AFTER_DRAFT: Record<string, string> = {
  market_not_editable: 'submitted',
  market_already_open: 'open',
  market_closed: 'closed',
  market_pending_resolution: 'pending_resolution',
  market_already_approved: 'approved',
}

const SAVE_STATUS_TEXT: Record<SaveStatus, { text: string; className: string } | null> = {
  idle: null,
  saving: { text: 'Saving…', className: 'text-subtle' },
  saved: { text: 'Saved', className: 'text-success' },
  error: { text: 'Save failed', className: 'text-danger' },
}

function createDefaultForm(): FormState {
  return {
    question: '',
    description: '',
    outcomes: [
      { id: crypto.randomUUID(), label: 'Yes' },
      { id: crypto.randomUUID(), label: 'No' },
    ],
    closeTime: '',
    resolutionTime: '',
    criteria: '',
    sources: [{ id: crypto.randomUUID(), url: '', label: '' }],
    liquidityB: '',
    seedSubsidy: '',
  }
}

function toISO(s: string): string | undefined {
  if (!s) return undefined
  try { return new Date(s).toISOString() } catch { return undefined }
}

function parseNum(s: string): number | undefined {
  const n = parseFloat(s)
  return isNaN(n) ? undefined : n
}

// Spec [FE][1.1] point 11: round to 4dp before sending to avoid a 422 on the
// fifth-decimal boundary check the server enforces.
function roundPrice(s: string): number | undefined {
  const n = parseFloat(s)
  if (isNaN(n)) return undefined
  return Math.round(n * 10000) / 10000
}

// What is wrong with a pricing input, if anything. The server refuses the
// whole save for a zero, negative or oversized value, so none of the other
// edits would be saved either — catch it here and don't send the field.
function priceInputError(s: string): string | undefined {
  if (!s.trim()) return undefined
  const n = roundPrice(s)
  if (n === undefined) return 'Enter a number.'
  if (n <= 0) return 'Must be greater than zero.'
  if (n >= PRICE_LIMIT) return 'That number is too large.'
  return undefined
}

function sendablePrice(s: string): number | undefined {
  return priceInputError(s) ? undefined : roundPrice(s)
}

// Everything POST /markets gets from the form, apart from draft_key and status.
function saveFields(f: FormState) {
  return {
    question: f.question || undefined,
    description: f.description || undefined,
    outcomes: f.outcomes.map(o => ({ label: o.label })),
    close_time: toISO(f.closeTime),
    resolution_time: toISO(f.resolutionTime),
    resolution_criteria: f.criteria || undefined,
    resolution_sources: sentSources(f)
      .map(s => ({ url: s.url.trim(), label: s.label.trim() || undefined })),
    liquidity_b: sendablePrice(f.liquidityB),
    seed_subsidy: sendablePrice(f.seedSubsidy),
  }
}

// Rows with a blank URL are not sent, so the server's resolution_sources[k]
// is the k-th row that has one — not necessarily the k-th row on screen.
function sentSources(f: FormState) {
  return f.sources.filter(s => s.url.trim())
}

/** The red × that removes one row of a repeating list. */
function RemoveRowButton({ label, className = '', onClick }: { label: string; className?: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} className={`cursor-pointer text-xl leading-[50px] text-danger ${className}`}>
      ×
    </button>
  )
}

export default function CreateMarketPage() {
  const navigate = useNavigate()
  const [draftKey] = useState(() => crypto.randomUUID())

  const [form, setForm] = useState<FormState>(createDefaultForm)
  const formRef = useRef<FormState>(form)
  useEffect(() => { formRef.current = form }, [form])
  // What the server last accepted. It starts as the blank form, so opening
  // the page and walking away creates no draft, and an autosave with nothing
  // new to say is skipped (market-service.md, notes for [FE][1.1], point 2).
  const lastSavedRef = useRef(JSON.stringify(saveFields(form)))

  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const [marketId, setMarketId] = useState<string | null>(null)
  const [marketStatus, setMarketStatus] = useState('draft')
  const marketStatusRef = useRef('draft')
  useEffect(() => { marketStatusRef.current = marketStatus }, [marketStatus])

  const [lastSave, setLastSave] = useState<SaveMarketResponse | null>(null)
  // Row ids in the order they were sent, so a hint for outcomes[k] or
  // resolution_sources[k] lands on the row it was about, even after rows are
  // added or removed and before the next save catches up.
  const [sentIds, setSentIds] = useState<{ outcomes: string[]; sources: string[] }>({ outcomes: [], sources: [] })
  const [submitError, setSubmitError] = useState('')
  const [publishError, setPublishError] = useState('')
  const [publishDetails, setPublishDetails] = useState<BlockingHint[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const isSavingRef = useRef(false)

  const getHint = (field: string) =>
    [...publishDetails, ...(lastSave?.blocking_submission ?? [])].find(b => b.field === field)?.message

  const doSave = useCallback(async (status: 'draft' | 'submitted') => {
    const f = formRef.current
    const fields = saveFields(f)
    const snapshot = JSON.stringify(fields)
    // Autosave skips if nothing changed or another save is in flight; manual
    // submit always proceeds.
    if (status === 'draft' && (snapshot === lastSavedRef.current || isSavingRef.current)) return null
    isSavingRef.current = true
    setSaveStatus('saving')
    try {
      const res = await saveMarket({ draft_key: draftKey, status, ...fields })
      lastSavedRef.current = snapshot
      setMarketId(res.market.id)
      setMarketStatus(res.market.status)
      setLastSave(res)
      setSentIds({ outcomes: f.outcomes.map(o => o.id), sources: sentSources(f).map(s => s.id) })
      setSaveStatus('saved')
      return res
    } catch (err) {
      const code = errorCode(err)
      const movedOnTo = code ? STATUS_AFTER_DRAFT[code] : undefined
      if (movedOnTo) {
        // Another session moved the market past draft: lock the form and let
        // the autosave interval stop.
        setMarketStatus(movedOnTo)
        setSaveStatus('idle')
        return null
      }
      setSaveStatus('error')
      throw err
    } finally {
      isSavingRef.current = false
    }
  }, [draftKey])

  useEffect(() => {
    const id = setInterval(() => {
      if (marketStatusRef.current !== 'draft') return
      doSave('draft').catch(() => {})
    }, 3000)
    return () => clearInterval(id)
  }, [doSave])

  const handleSubmit = async () => {
    setSubmitting(true)
    setSubmitError('')
    try {
      await doSave('submitted')
    } catch {
      setSubmitError('Submission failed. Check all fields are complete and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  const handlePublish = async () => {
    if (!marketId || publishing) return
    setPublishing(true)
    setPublishError('')
    setPublishDetails([])
    try {
      await publishMarket(marketId)
      navigate('/markets')
    } catch (err) {
      const details = errorDetails(err)
      if (details.length) setPublishDetails(details)
      setPublishError('Publish failed. Please try again.')
      setPublishing(false)
    }
  }

  const updateOutcome = (id: string, value: string) =>
    setForm(f => ({ ...f, outcomes: f.outcomes.map(o => o.id === id ? { ...o, label: value } : o) }))
  const addOutcome = () =>
    setForm(f => ({ ...f, outcomes: [...f.outcomes, { id: crypto.randomUUID(), label: '' }] }))
  const removeOutcome = (id: string) =>
    setForm(f => ({ ...f, outcomes: f.outcomes.filter(o => o.id !== id) }))

  const updateSource = (id: string, field: 'url' | 'label', value: string) =>
    setForm(f => ({ ...f, sources: f.sources.map(s => s.id === id ? { ...s, [field]: value } : s) }))
  const addSource = () =>
    setForm(f => ({ ...f, sources: [...f.sources, { id: crypto.randomUUID(), url: '', label: '' }] }))
  const removeSource = (id: string) =>
    setForm(f => ({ ...f, sources: f.sources.filter(s => s.id !== id) }))

  const isSubmitted = marketStatus === 'submitted'
  const isLocked = marketStatus !== 'draft'

  const questionHint = getHint('question')
  const outcomesHint = getHint('outcomes')
  const closeTimeHint = getHint('close_time')
  const resolutionTimeHint = getHint('resolution_time')
  const criteriaHint = getHint('resolution_criteria')
  const resolutionSourcesHint = getHint('resolution_sources')
  const seedSubsidyHint = priceInputError(form.seedSubsidy) ?? getHint('seed_subsidy')
  const liquidityBHint = priceInputError(form.liquidityB) ?? getHint('liquidity_b')
  const cannotSubmit = submitting
    || (lastSave?.blocking_submission.length ?? 0) > 0
    || priceInputError(form.seedSubsidy) !== undefined
    || priceInputError(form.liquidityB) !== undefined
  const seedSubsidyNum = parseNum(form.seedSubsidy)

  return (
    <AppLayout width="max-w-[800px]">
      <div className="mb-8 flex items-start justify-between">
        <div>
          <BackLink to="/markets" label="Markets" className="mb-1.5 block" />
          <PageTitle>New Market</PageTitle>
        </div>
        {SAVE_STATUS_TEXT[saveStatus] && (
          <span className={`pt-7 text-[13px] ${SAVE_STATUS_TEXT[saveStatus].className}`}>
            {SAVE_STATUS_TEXT[saveStatus].text}
          </span>
        )}
      </div>

      <SectionCard title="Question" className="mb-5">
        <div className="flex flex-col gap-4">
          <Field id="question" label="Question *" hint={questionHint}>
            <TextInput
              id="question"
              invalid={!!questionHint}
              value={form.question}
              onChange={e => setForm(f => ({ ...f, question: e.target.value }))}
              placeholder="e.g. Will Singapore core inflation be below 2% for December 2026?"
              disabled={isLocked}
            />
          </Field>
          <Field id="description" label="Description (optional)">
            <TextArea
              id="description"
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="Additional context for traders"
              disabled={isLocked}
            />
          </Field>
        </div>
      </SectionCard>

      <SectionCard title="Outcomes" className="mb-5">
        {outcomesHint && <p className="-mt-3 mb-4 text-xs text-subtle">{outcomesHint}</p>}
        <div className="flex flex-col gap-2">
          {form.outcomes.map((outcome, i) => {
            const k = sentIds.outcomes.indexOf(outcome.id)
            const outcomeHint = k >= 0 ? getHint(`outcomes[${k}].label`) : undefined
            const initialPrice = k >= 0 ? lastSave?.market.outcomes[k]?.initial_price : undefined
            return (
              <div key={outcome.id} className="flex items-start gap-2">
                <div className="flex-1">
                  <Field id={`outcome-${i}`} label={`Outcome ${i + 1}`} hint={outcomeHint}>
                    <TextInput
                      id={`outcome-${i}`}
                      invalid={!!outcomeHint}
                      value={outcome.label}
                      onChange={e => updateOutcome(outcome.id, e.target.value)}
                      placeholder={DEFAULT_OUTCOME_PLACEHOLDERS[i] ?? 'Outcome label'}
                      disabled={isLocked}
                    />
                  </Field>
                </div>
                {initialPrice != null && (
                  <span className="pt-[22px] text-xs leading-[50px] whitespace-nowrap text-muted">
                    {(initialPrice * 100).toFixed(1)}% start
                  </span>
                )}
                {form.outcomes.length > 2 && !isLocked && (
                  <RemoveRowButton label={`Remove outcome ${i + 1}`} className="pt-[22px]" onClick={() => removeOutcome(outcome.id)} />
                )}
              </div>
            )
          })}
        </div>
        {form.outcomes.length < 10 && !isLocked && (
          <Button variant="dashed" size="xs" onClick={addOutcome} className="mt-2">+ Add outcome</Button>
        )}
      </SectionCard>

      <SectionCard title="Timeline" className="mb-5">
        <div className="grid grid-cols-2 gap-4">
          <Field id="close-time" label="Closes at *" hint={closeTimeHint}>
            <TextInput
              id="close-time"
              type="datetime-local"
              invalid={!!closeTimeHint}
              value={form.closeTime}
              onChange={e => setForm(f => ({ ...f, closeTime: e.target.value }))}
              disabled={isLocked}
            />
          </Field>
          <Field id="resolution-time" label="Resolves by *" hint={resolutionTimeHint}>
            <TextInput
              id="resolution-time"
              type="datetime-local"
              invalid={!!resolutionTimeHint}
              value={form.resolutionTime}
              onChange={e => setForm(f => ({ ...f, resolutionTime: e.target.value }))}
              disabled={isLocked}
            />
          </Field>
        </div>
      </SectionCard>

      <SectionCard title="Resolution" className="mb-5">
        <div className="flex flex-col gap-5">
          <Field id="criteria" label="Resolution criteria *" hint={criteriaHint}>
            <TextArea
              id="criteria"
              invalid={!!criteriaHint}
              value={form.criteria}
              onChange={e => setForm(f => ({ ...f, criteria: e.target.value }))}
              placeholder="Exactly what determines the outcome? e.g. Resolves YES if the MAS print for December 2026, as first published, is strictly below 2.0%."
              disabled={isLocked}
            />
          </Field>

          <div>
            <p className="mb-2 text-[13px] font-semibold text-smu-navy">Resolution sources *</p>
            {resolutionSourcesHint && <p className="-mt-1 mb-3 text-xs text-subtle">{resolutionSourcesHint}</p>}
            <div className="flex flex-col gap-2.5">
              {form.sources.map((src, i) => {
                const k = sentIds.sources.indexOf(src.id)
                const urlHint = k >= 0 ? getHint(`resolution_sources[${k}].url`) : undefined
                return (
                  <div key={src.id} className="flex items-start gap-2">
                    <div className="flex-[2]">
                      <Field id={`src-url-${i}`} label={i === 0 ? 'URL' : ' '} hint={urlHint}>
                        <TextInput
                          id={`src-url-${i}`}
                          invalid={!!urlHint}
                          value={src.url}
                          onChange={e => updateSource(src.id, 'url', e.target.value)}
                          placeholder="https://…"
                          disabled={isLocked}
                        />
                      </Field>
                    </div>
                    <div className="flex-1">
                      <Field id={`src-label-${i}`} label={i === 0 ? 'Label (optional)' : ' '}>
                        <TextInput
                          id={`src-label-${i}`}
                          value={src.label}
                          onChange={e => updateSource(src.id, 'label', e.target.value)}
                          placeholder="e.g. MAS statistics"
                          disabled={isLocked}
                        />
                      </Field>
                    </div>
                    {form.sources.length > 1 && !isLocked && (
                      <RemoveRowButton label={`Remove source ${i + 1}`} className={i === 0 ? 'mt-[22px]' : ''} onClick={() => removeSource(src.id)} />
                    )}
                  </div>
                )
              })}
            </div>
            {!isLocked && (
              <Button variant="dashed" size="xs" onClick={addSource} className="mt-2">+ Add source</Button>
            )}
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Pricing" className="mb-5">
        <div className="grid grid-cols-2 gap-4">
          <Field id="seed-subsidy" label="Seed subsidy (credits) *" hint={seedSubsidyHint}>
            <TextInput
              id="seed-subsidy"
              type="number"
              min={0}
              step="0.01"
              invalid={!!seedSubsidyHint}
              value={form.seedSubsidy}
              onChange={e => setForm(f => ({ ...f, seedSubsidy: e.target.value }))}
              placeholder="e.g. 250"
              disabled={isLocked}
            />
          </Field>
          <Field id="liquidity-b" label="Liquidity parameter b" hint={liquidityBHint ?? 'Leave blank to use the server default'}>
            <TextInput
              id="liquidity-b"
              type="number"
              min={0}
              step="0.01"
              invalid={!!liquidityBHint}
              value={form.liquidityB}
              onChange={e => setForm(f => ({ ...f, liquidityB: e.target.value }))}
              placeholder="Default: 100"
              disabled={isLocked}
            />
          </Field>
        </div>
        {lastSave?.market.max_platform_loss != null && (
          <div aria-label="max platform loss" className="mt-4 rounded-control bg-smu-cream px-4 py-3 text-[13px] text-muted">
            Max platform loss:{' '}
            <strong className="text-smu-navy">{lastSave.market.max_platform_loss.toFixed(4)} credits</strong>
            {seedSubsidyNum != null && (
              seedSubsidyNum >= lastSave.market.max_platform_loss
                ? <span className="ml-3 font-semibold text-success">✓ Seed subsidy covers worst case</span>
                : <span className="ml-3 font-semibold text-warning">⚠ Seed subsidy below max loss</span>
            )}
          </div>
        )}
      </SectionCard>

      <div className="flex flex-col items-start gap-3 pb-12">
        {submitError && <p role="alert" className="text-[13px] text-danger">{submitError}</p>}

        {!isLocked && (
          <Button variant="primary" size="lg" onClick={handleSubmit} disabled={cannotSubmit}>
            {submitting ? 'Submitting…' : 'Submit for Review'}
          </Button>
        )}

        {isSubmitted && (
          <>
            {publishError && <p role="alert" className="text-[13px] text-danger">{publishError}</p>}
            <div className="flex items-center gap-2 text-[13px] text-muted">
              <Badge tone="warning">Submitted</Badge>
              Market is ready to be published.
            </div>
            <Button variant="gold" size="lg" onClick={handlePublish} disabled={publishing}>
              {publishing ? 'Publishing…' : 'Publish Market'}
            </Button>
          </>
        )}
      </div>
    </AppLayout>
  )
}
