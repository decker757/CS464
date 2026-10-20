import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { errorCode, errorDetails } from '../../api/errors'
import {
  type BlockingHint,
  type MarketOut,
  type SaveMarketResponse,
  getMyMarket,
  saveMarket,
} from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import type { AdminMarketStatus } from '../../components/markets/marketStatus'
import BackLink from '../../components/ui/BackLink'
import Badge from '../../components/ui/Badge'
import Button from '../../components/ui/Button'
import SectionCard from '../../components/ui/SectionCard'
import Field from '../../components/ui/Field'
import PageTitle from '../../components/ui/PageTitle'
import TextArea from '../../components/ui/TextArea'
import TextInput from '../../components/ui/TextInput'
import { usePublishMarket } from '../../hooks/usePublishMarket'

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

// Every status a refused save can report: anything but the one it was asking
// to write. Typed off the API's own union, so a sixth code cannot be added to
// STATUS_AFTER_DRAFT without a sentence in LOCKED_BECAUSE to go with it.
type LockedStatus = Exclude<AdminMarketStatus, 'draft'>

// A save refused with one of these codes means the market already has this status.
const STATUS_AFTER_DRAFT: Record<string, LockedStatus> = {
  market_not_editable: 'submitted',
  market_already_open: 'open',
  market_closed: 'closed',
  market_pending_resolution: 'pending_resolution',
  market_already_approved: 'approved',
}

// What the market turned out to be when a save was refused, for the banner
// that explains why the form just went read-only. Publication is one way (ADR
// 0008), and this form locks itself the moment a market leaves draft. The one
// way back in is a publish refused on the terms, which reopens a submitted
// market for a resubmission (#381).
const LOCKED_BECAUSE: Record<LockedStatus, string> = {
  submitted: 'This market has already been submitted for review.',
  open: 'This market has already been published and is live for traders.',
  closed: 'This market has already been published, and it has closed.',
  pending_resolution: 'This market already has an outcome waiting for a second administrator.',
  approved: 'This market already has an approved outcome.',
  settled: 'This market has already been settled and paid out.',
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

// The other direction, for a market loaded back into the form: a
// datetime-local input wants local wall-clock time to the minute and no zone,
// so `toISOString` would show an admin in Singapore a time eight hours off
// their own. Minutes, because that is all the input offers — so a resumed
// market whose close time carries seconds loses them on its next save.
function toDateTimeInput(iso: string | null): string {
  if (!iso) return ''
  const when = new Date(iso)
  if (isNaN(when.getTime())) return ''
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
    + `T${pad(when.getHours())}:${pad(when.getMinutes())}`
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

// --- a market the server already holds, coming back into the form (#348) ----

// Its fields, as this form's. Row ids are the server's own, so a blocking hint
// for outcomes[k] lands on the row it is about from the first render. An empty
// list falls back to the blank form's rows: an empty row is how a new outcome
// or source is typed, and a market saved without either would otherwise offer
// nothing to type into.
function formOf(market: MarketOut): FormState {
  const blank = createDefaultForm()
  return {
    question: market.question ?? '',
    description: market.description ?? '',
    outcomes: market.outcomes.length
      ? market.outcomes.map(outcome => ({ id: outcome.id, label: outcome.label }))
      : blank.outcomes,
    closeTime: toDateTimeInput(market.close_time),
    resolutionTime: toDateTimeInput(market.resolution_time),
    criteria: market.resolution_criteria ?? '',
    sources: market.resolution_sources.length
      ? market.resolution_sources.map(source => ({ id: source.id, url: source.url, label: source.label ?? '' }))
      : blank.sources,
    liquidityB: market.liquidity_b == null ? '' : String(market.liquidity_b),
    seedSubsidy: market.seed_subsidy == null ? '' : String(market.seed_subsidy),
  }
}

// The status it is frozen at, or undefined while it is still a draft this form
// may write to. Same shape as `statusDisplay`: a status this frontend has not
// been taught yet locks nothing.
function lockedStatusOf(status: string): LockedStatus | undefined {
  return Object.hasOwn(LOCKED_BECAUSE, status) ? status as LockedStatus : undefined
}

// What became of the edits that were open when a save discovered the market
// had moved on. Nothing, when the market was already past draft before this
// page loaded it: there was never anything typed here to lose.
function describeLostEdits(lostEdits: boolean | null): string {
  if (lostEdits === null) return ''
  return lostEdits
    ? 'The edits you had open when that happened were not saved.'
    : 'Everything you typed had already been saved.'
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
  // Present on /admin/markets/:id/edit, absent on /admin/markets/new. The
  // whole difference between resuming a market and creating one.
  const { id: existingMarketId } = useParams<{ id: string }>()
  // Seeded fresh for a new market and replaced by the loaded market's own key,
  // because that key is what makes the next save continue that market rather
  // than create a second one (#348).
  const [draftKey, setDraftKey] = useState<string>(() => crypto.randomUUID())
  // Which market this page last failed to read. Named rather than a flag, so
  // an address that moves to another market is not met with the previous
  // one's error, and so a read that failed is not retried on every render.
  const [failedMarketId, setFailedMarketId] = useState<string | null>(null)
  // True once this page has read a market it did not create here: a resumed
  // draft, or one reopened after a reload. The heading says which job the
  // page is doing, and must not change under an admin's hands the moment
  // their first autosave gives a new market an address of its own.
  const [hasLoadedMarket, setHasLoadedMarket] = useState(false)

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
  // The per-field reasons from the last refused submission, or from a publish
  // refused on the terms: the same `details` shape (ADR 0008), so the hints
  // land under the same fields either way, and the next submission's answer
  // replaces them.
  const [refusalDetails, setRefusalDetails] = useState<BlockingHint[]>([])
  const [submitting, setSubmitting] = useState(false)
  // When the server last accepted a save, so "Saved" does not read the same
  // three seconds and ten minutes later.
  const [savedAt, setSavedAt] = useState<Date | null>(null)
  // Set once, when a save is refused because the market has moved past draft,
  // or when the market this page loaded was already past it. `lostEdits` is
  // null in the second case: nothing had been typed here to lose.
  const [lockOut, setLockOut] = useState<{ status: LockedStatus; lostEdits: boolean | null } | null>(null)
  const isSavingRef = useRef(false)
  // Set when a publish is refused because the terms no longer pass (ADR 0008),
  // which reopens the fields. Cleared once a resubmission is accepted, or is
  // refused because the market has moved on (a status refusal such as
  // market_already_open, which locks the form instead); kept on a refusal that
  // throws (draft_incomplete, a 5xx, the network), so the admin can correct
  // and resubmit again. market_service takes a resubmission of a submitted
  // market and re-runs every rule; only a save asking for `draft` is refused
  // (ADR 0004), and the autosave interval never sends one, because it runs
  // only while the status is draft (#381).
  const [isCorrecting, setIsCorrecting] = useState(false)

  // The same hook the markets list publishes through, so both screens send
  // the publish and read its refusals in one place — which matters more now
  // this form survives a reload and can sit open long enough for somebody else
  // to publish the market from the list. The one refusal they answer
  // differently is draft_incomplete: the list shows the hook's message, and
  // this form passes the callback below and reopens its fields instead.
  const {
    publishing,
    formError: publishError,
    superseded: publishSuperseded,
    handlePublish,
  } = usePublishMarket(marketId, () => navigate('/markets'), details => {
    // The reasons go where a refused submission's do, so the next answer —
    // accepted or refused — replaces them rather than sitting beside them.
    setRefusalDetails(details)
    // It said read-only, which stops being true here.
    setLockOut(null)
    setIsCorrecting(true)
  })

  // Derived, not stored: this page is loading exactly while the address names
  // a market it has not read yet. Both routes render the same element in the
  // same position, so moving between them reuses this component rather than
  // remounting it — a flag seeded at mount would stay false, leaving the form
  // on screen through the fetch for the answer to land on top of.
  const hasLoadFailed = !!existingMarketId && failedMarketId === existingMarketId
  const isLoadingMarket = !!existingMarketId && marketId !== existingMarketId && !hasLoadFailed

  const getHint = (field: string) =>
    [...refusalDetails, ...(lastSave?.blocking_submission ?? [])]
      .find(b => b.field === field)?.message

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
      // This response's blocking_submission is the whole truth about what is
      // still wrong, so the reasons from an earlier refusal go with it. Left
      // behind, a fixed field keeps its red message until the next submit.
      setRefusalDetails([])
      setSentIds({ outcomes: f.outcomes.map(o => o.id), sources: sentSources(f).map(s => s.id) })
      setSavedAt(new Date())
      setSaveStatus('saved')
      // The create page has just created a market while the address bar still
      // says "new", and a reload of that page mints another draft_key and
      // creates a second market — the other half of #348. Put the market's
      // own address in the bar instead, and let the browser be the resume
      // point. `replace`, so there is no Back to the page that would do it
      // again; and only once, because `existingMarketId` is a dependency of
      // this callback and is set by the time the next save runs.
      if (!existingMarketId) {
        navigate(`/admin/markets/${res.market.id}/edit`, { replace: true })
      }
      return res
    } catch (err) {
      const code = errorCode(err)
      const movedOnTo = code ? STATUS_AFTER_DRAFT[code] : undefined
      if (movedOnTo) {
        // Another session moved the market past draft: lock the form, say so,
        // and let the autosave interval stop. Ask the form as it stands now,
        // not the snapshot this save set out with — nothing disables the
        // fields until this answer arrives, so a manual submit can be typed
        // over while it is in flight, and that typing is what gets lost.
        const lostEdits = JSON.stringify(saveFields(formRef.current)) !== lastSavedRef.current
        setMarketStatus(movedOnTo)
        setLockOut({ status: movedOnTo, lostEdits })
        setSaveStatus('idle')
        return null
      }
      setSaveStatus('error')
      throw err
    } finally {
      isSavingRef.current = false
    }
  }, [draftKey, existingMarketId, navigate])

  // The market this page was opened on, back into the form ([1.1] #1's reload,
  // #348). Everything the next save needs comes from here: the draft_key it
  // continues, the id Publish names, and the status that decides whether this
  // form may write at all.
  useEffect(() => {
    if (!existingMarketId) return
    // Already the market on screen, which is where the redirect above lands:
    // re-reading it would only fetch back what that save just wrote, over
    // whatever has been typed since.
    if (marketId === existingMarketId) return
    // One read per market. Without this the failure below changes a
    // dependency and asks for the same market again.
    if (failedMarketId === existingMarketId) return
    let cancelled = false
    getMyMarket(existingMarketId)
      .then(market => {
        if (cancelled) return
        const loaded = formOf(market)
        setForm(loaded)
        // What the server already holds, so the next autosave tick does not
        // re-send the market it has just read back.
        lastSavedRef.current = JSON.stringify(saveFields(loaded))
        setDraftKey(market.draft_key)
        setMarketId(market.id)
        setMarketStatus(market.status)
        setHasLoadedMarket(true)
        // The market as the server has it, so the starting prices and the max
        // platform loss are on screen before anything is typed. Nothing is
        // blocking submission as far as this response says — only a save
        // answers that, and the first one will.
        setLastSave({ market, blocking_submission: [] })
        setSentIds({ outcomes: loaded.outcomes.map(o => o.id), sources: sentSources(loaded).map(s => s.id) })
        const frozenAt = lockedStatusOf(market.status)
        if (frozenAt) setLockOut({ status: frozenAt, lostEdits: null })
      })
      .catch(() => { if (!cancelled) setFailedMarketId(existingMarketId) })
    return () => { cancelled = true }
  }, [existingMarketId, marketId, failedMarketId])

  useEffect(() => {
    // Nothing autosaves until the market has landed. The form on screen is
    // not the market's yet and `draftKey` is not the market's either, so a
    // save from here would create a second market rather than continue this
    // one — which is half of what #348 was.
    if (isLoadingMarket) return
    const timer = setInterval(() => {
      if (marketStatusRef.current !== 'draft') return
      doSave('draft').catch(() => {})
    }, 3000)
    return () => clearInterval(timer)
  }, [doSave, isLoadingMarket])

  const handleRetrySave = () => {
    // Same guard as the interval: nothing may write a draft over a market
    // that has moved on.
    if (marketStatusRef.current !== 'draft') return
    doSave('draft').catch(() => {})
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    setSubmitError('')
    setRefusalDetails([])
    try {
      await doSave('submitted')
      setIsCorrecting(false)
    } catch (err) {
      const details = errorDetails(err)
      setRefusalDetails(details)
      setSubmitError(details.length
        ? 'This market is not ready to submit. Each field above says what to change.'
        : 'Submission failed. Check all fields are complete and try again.')
    } finally {
      setSubmitting(false)
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
  const isLocked = marketStatus !== 'draft' && !isCorrecting
  const submitLabel = isCorrecting ? 'Resubmit for Review' : 'Submit for Review'
  // "the fields marked above" only when the server named some. A refusal that
  // names none — an empty `details`, a 5xx, the network — marks nothing, and
  // the advice would then point at marks that are not there.
  const correctionAdvice = refusalDetails.length > 0
    ? 'Correct the fields marked above and submit the market again.'
    : 'Correct them and submit the market again.'
  // A published market has a page traders can see. A submitted one has none
  // yet (#354), so the banner points at the admin list for that case.
  const lockedMarketLink = lockOut && lockOut.status !== 'submitted' && marketId
    ? { to: `/markets/${marketId}`, label: 'View the market' }
    : { to: '/admin/markets', label: 'Go to All Markets' }

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

  if (isLoadingMarket) {
    return (
      <AppLayout width="max-w-[800px]">
        <p className="text-sm text-muted">Loading market…</p>
      </AppLayout>
    )
  }

  // No form at all, rather than a blank one: a blank form here would be typed
  // into and autosaved as a second market. A market belonging to another
  // administrator is a 404 and lands here too (market-service.md).
  if (hasLoadFailed) {
    return (
      <AppLayout width="max-w-[800px]">
        <BackLink to="/admin/markets" label="All Markets" className="mb-4 block" />
        <p role="alert" className="text-sm text-danger">Failed to load this market. Please try again.</p>
      </AppLayout>
    )
  }

  return (
    <AppLayout width="max-w-[800px]">
      <div className="mb-8 flex items-start justify-between">
        <div>
          <BackLink to="/admin/markets" label="All Markets" className="mb-1.5 block" />
          <PageTitle>{hasLoadedMarket ? 'Edit Market' : 'New Market'}</PageTitle>
        </div>
        {/* Always rendered, so a screen reader is already watching the region
            when the first message appears in it. */}
        <div role="status" className="pt-7 text-[13px]">
          {saveStatus === 'saving' && <span className="text-subtle">Saving…</span>}
          {saveStatus === 'saved' && savedAt && (
            <span className="text-success">Saved at {savedAt.toLocaleTimeString('en-SG', { hour: '2-digit', minute: '2-digit' })}</span>
          )}
          {/* Not while correcting: Retry sends a draft save, which a submitted
              market refuses, and the refused resubmission says so below. */}
          {saveStatus === 'error' && !isCorrecting && (
            <span className="flex items-center gap-2 text-danger">
              Save failed
              <Button variant="outline" size="xs" onClick={handleRetrySave}>Retry</Button>
            </span>
          )}
        </div>
      </div>

      {lockOut && (
        <div role="alert" className="mb-6 rounded-control border border-warning/40 bg-warning/10 px-4 py-3 text-[13px] text-warning-strong">
          <p>
            {LOCKED_BECAUSE[lockOut.status]} The form is now read-only.{' '}
            {describeLostEdits(lockOut.lostEdits)}
          </p>
          <Link to={lockedMarketLink.to} className="mt-1 inline-block font-semibold underline">
            {lockedMarketLink.label}
          </Link>
        </div>
      )}

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
                    {/* Only the first row shows a visible heading, so the rows
                        stay compact. Every row still needs a name of its own
                        in the accessibility tree, which is what aria-label
                        gives it (WCAG 3.3.2). */}
                    <div className="flex-[2]">
                      <Field id={`src-url-${i}`} label={i === 0 ? 'URL' : ' '} hint={urlHint}>
                        <TextInput
                          id={`src-url-${i}`}
                          aria-label={`Source ${i + 1} URL`}
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
                          aria-label={`Source ${i + 1} label (optional)`}
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
        {isCorrecting && (
          <p role="alert" className="text-[13px] text-danger">
            These terms no longer pass the checks for publishing. {correctionAdvice} Nothing
            you change here is saved until the market is resubmitted.
          </p>
        )}
        {submitError && <p role="alert" className="text-[13px] text-danger">{submitError}</p>}

        {!isLocked && (
          <Button variant="primary" size="lg" onClick={handleSubmit} disabled={cannotSubmit}>
            {submitting ? 'Submitting…' : submitLabel}
          </Button>
        )}

        {isSubmitted && !isCorrecting && (
          <>
            {publishError && (
              <div role="alert" className="flex items-center gap-2 text-[13px] text-danger">
                <span>{publishError}</span>
                {/* The market has moved past submitted, so nothing on this
                    page is true any more and no retry can succeed. Same
                    control the markets list's row offers for these codes. */}
                {publishSuperseded && (
                  <Button variant="outline" size="xs" onClick={() => window.location.reload()}>Reload</Button>
                )}
              </div>
            )}
            {!publishSuperseded && (
              <>
                <div className="flex items-center gap-2 text-[13px] text-muted">
                  <Badge tone="warning">Submitted</Badge>
                  Market is ready to be published.
                </div>
                <Button variant="gold" size="lg" onClick={handlePublish} disabled={publishing}>
                  {publishing ? 'Publishing…' : 'Publish Market'}
                </Button>
              </>
            )}
          </>
        )}
      </div>
    </AppLayout>
  )
}
