import type { CSSProperties } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import axios from 'axios'
import type { ApiError } from '../../api/errors'
import {
  type BlockingHint,
  type SaveMarketResponse,
  publishMarket,
  saveMarket,
} from '../../api/marketApi'
import AppNavbar from '../../components/AppNavbar'
import Field from '../../components/auth/Field'
import { focusHandlers, inputBase } from '../../components/auth/inputStyles'
import { CREAM, GOLD, NAV } from '../../theme/colors'

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

const sectionCard: CSSProperties = {
  backgroundColor: '#fff',
  border: '1px solid rgba(182,145,70,0.15)',
  borderRadius: 16,
  padding: '24px 28px',
  marginBottom: 20,
  boxShadow: '0 2px 8px rgba(21,30,85,0.05)',
}

const sectionTitle: CSSProperties = {
  fontSize: 15,
  fontWeight: 700,
  color: NAV,
  margin: '0 0 20px',
  paddingBottom: 12,
  borderBottom: '1px solid rgba(182,145,70,0.12)',
}

function textareaStyle(hasError: boolean): CSSProperties {
  return {
    width: '100%',
    padding: '12px 14px',
    fontSize: 15,
    borderRadius: 10,
    border: `1px solid ${hasError ? '#dc2626' : '#e2e0da'}`,
    backgroundColor: '#fff',
    color: NAV,
    outline: 'none',
    boxSizing: 'border-box',
    transition: 'border-color 0.15s, box-shadow 0.15s',
    resize: 'vertical',
    fontFamily: 'inherit',
    minHeight: 100,
  }
}

function textareaFocus(hasError: boolean) {
  return {
    onFocus: (e: React.FocusEvent<HTMLTextAreaElement>) => {
      e.target.style.borderColor = NAV
      e.target.style.boxShadow = '0 0 0 3px rgba(21,30,85,0.08)'
    },
    onBlur: (e: React.FocusEvent<HTMLTextAreaElement>) => {
      e.target.style.borderColor = hasError ? '#dc2626' : '#e2e0da'
      e.target.style.boxShadow = 'none'
    },
  }
}

const ghostBtn: CSSProperties = {
  fontSize: 13,
  color: NAV,
  background: 'none',
  border: '1px dashed rgba(21,30,85,0.3)',
  borderRadius: 8,
  padding: '6px 14px',
  cursor: 'pointer',
  marginTop: 8,
}

export default function CreateMarketPage() {
  const navigate = useNavigate()
  const [draftKey] = useState(() => crypto.randomUUID())

  const [form, setForm] = useState<FormState>(createDefaultForm)
  const formRef = useRef<FormState>(form)
  useEffect(() => { formRef.current = form }, [form])

  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const [marketId, setMarketId] = useState<string | null>(null)
  const [marketStatus, setMarketStatus] = useState('draft')
  const marketStatusRef = useRef('draft')
  useEffect(() => { marketStatusRef.current = marketStatus }, [marketStatus])

  const [lastSave, setLastSave] = useState<SaveMarketResponse | null>(null)
  const [submitError, setSubmitError] = useState('')
  const [publishError, setPublishError] = useState('')
  const [publishDetails, setPublishDetails] = useState<BlockingHint[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const isSavingRef = useRef(false)

  const getHint = (field: string) =>
    [...publishDetails, ...(lastSave?.blocking_submission ?? [])].find(b => b.field === field)?.message

  const doSave = useCallback(async (status: 'draft' | 'submitted') => {
    // Autosave skips if another save is in flight; manual submit always proceeds.
    if (isSavingRef.current && status === 'draft') return null
    isSavingRef.current = true
    setSaveStatus('saving')
    const f = formRef.current
    const body = {
      draft_key: draftKey,
      status,
      question: f.question || undefined,
      description: f.description || undefined,
      outcomes: f.outcomes.map(o => ({ label: o.label })),
      close_time: toISO(f.closeTime),
      resolution_time: toISO(f.resolutionTime),
      resolution_criteria: f.criteria || undefined,
      resolution_sources: f.sources
        .filter(s => s.url.trim())
        .map(s => ({ url: s.url.trim(), label: s.label.trim() || undefined })),
      liquidity_b: roundPrice(f.liquidityB),
      seed_subsidy: roundPrice(f.seedSubsidy),
    }
    try {
      const res = await saveMarket(body)
      setMarketId(res.market.id)
      setMarketStatus(res.market.status)
      setLastSave(res)
      setSaveStatus('saved')
      return res
    } catch (err) {
      if (axios.isAxiosError<ApiError>(err)) {
        const code = err.response?.data?.error?.code
        // These codes mean the market has moved past draft on another session.
        // Update local status so the autosave interval stops and the form locks.
        if (code === 'market_not_editable') { setMarketStatus('submitted'); setSaveStatus('idle'); return null }
        if (code === 'market_already_open') { setMarketStatus('open');      setSaveStatus('idle'); return null }
        if (code === 'market_closed')       { setMarketStatus('closed');    setSaveStatus('idle'); return null }
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
      if (axios.isAxiosError<ApiError>(err)) {
        const details = err.response?.data?.error?.details
        if (details?.length) setPublishDetails(details)
      }
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
  const seedSubsidyHint = getHint('seed_subsidy')
  const liquidityBHint = getHint('liquidity_b')
  const seedSubsidyNum = parseNum(form.seedSubsidy)

  return (
    <div style={{ minHeight: '100vh', backgroundColor: CREAM }}>
      <AppNavbar />
      <main style={{ maxWidth: 800, margin: '0 auto', padding: '48px 32px' }}>

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 32 }}>
          <div>
            <Link
              to="/markets"
              style={{ fontSize: 13, color: '#6b7280', textDecoration: 'none', display: 'block', marginBottom: 6 }}
            >
              ← Markets
            </Link>
            <h1 style={{ fontSize: 28, fontWeight: 800, color: NAV, margin: 0, letterSpacing: '-0.3px' }}>
              New Market
            </h1>
          </div>
          <div style={{ paddingTop: 28, fontSize: 13, textAlign: 'right' }}>
            {saveStatus === 'saving' && <span style={{ color: '#9ca3af' }}>Saving…</span>}
            {saveStatus === 'saved' && <span style={{ color: '#16a34a' }}>Saved</span>}
            {saveStatus === 'error' && <span style={{ color: '#dc2626' }}>Save failed</span>}
          </div>
        </div>

        {/* Section: Question */}
        <div style={sectionCard}>
          <h2 style={sectionTitle}>Question</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Field id="question" label="Question *" hint={questionHint}>
              <input
                id="question"
                style={inputBase(!!questionHint)}
                value={form.question}
                onChange={e => setForm(f => ({ ...f, question: e.target.value }))}
                placeholder="e.g. Will Singapore core inflation be below 2% for December 2026?"
                disabled={isLocked}
                {...focusHandlers(!!questionHint)}
              />
            </Field>
            <Field id="description" label="Description (optional)">
              <textarea
                id="description"
                style={textareaStyle(false)}
                value={form.description}
                onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                placeholder="Additional context for traders"
                disabled={isLocked}
                {...textareaFocus(false)}
              />
            </Field>
          </div>
        </div>

        {/* Section: Outcomes */}
        <div style={sectionCard}>
          <h2 style={sectionTitle}>Outcomes</h2>
          {outcomesHint && (
            <p style={{ fontSize: 12, color: '#9ca3af', margin: '-12px 0 16px' }}>{outcomesHint}</p>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {form.outcomes.map((outcome, i) => {
              const outcomeHint = getHint(`outcomes[${i}].label`)
              const initialPrice = lastSave?.market.outcomes[i]?.initial_price
              return (
                <div key={outcome.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <div style={{ flex: 1 }}>
                    <Field id={`outcome-${i}`} label={`Outcome ${i + 1}`} hint={outcomeHint}>
                      <input
                        id={`outcome-${i}`}
                        style={inputBase(!!outcomeHint)}
                        value={outcome.label}
                        onChange={e => updateOutcome(outcome.id, e.target.value)}
                        placeholder={i === 0 ? 'Yes' : i === 1 ? 'No' : 'Outcome label'}
                        disabled={isLocked}
                        {...focusHandlers(!!outcomeHint)}
                      />
                    </Field>
                  </div>
                  {initialPrice != null && (
                    <span style={{ paddingTop: 22, lineHeight: '50px', fontSize: 12, color: '#6b7280', whiteSpace: 'nowrap' }}>
                      {(initialPrice * 100).toFixed(1)}% start
                    </span>
                  )}
                  {form.outcomes.length > 2 && !isLocked && (
                    <button
                      type="button"
                      onClick={() => removeOutcome(outcome.id)}
                      aria-label={`Remove outcome ${i + 1}`}
                      style={{ paddingTop: 22, lineHeight: '50px', background: 'none', border: 'none', color: '#dc2626', cursor: 'pointer', fontSize: 20 }}
                    >
                      ×
                    </button>
                  )}
                </div>
              )
            })}
          </div>
          {form.outcomes.length < 10 && !isLocked && (
            <button type="button" onClick={addOutcome} style={ghostBtn}>
              + Add outcome
            </button>
          )}
        </div>

        {/* Section: Timeline */}
        <div style={sectionCard}>
          <h2 style={sectionTitle}>Timeline</h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <Field id="close-time" label="Closes at *" hint={closeTimeHint}>
              <input
                id="close-time"
                type="datetime-local"
                style={inputBase(!!closeTimeHint)}
                value={form.closeTime}
                onChange={e => setForm(f => ({ ...f, closeTime: e.target.value }))}
                disabled={isLocked}
                {...focusHandlers(!!closeTimeHint)}
              />
            </Field>
            <Field id="resolution-time" label="Resolves by *" hint={resolutionTimeHint}>
              <input
                id="resolution-time"
                type="datetime-local"
                style={inputBase(!!resolutionTimeHint)}
                value={form.resolutionTime}
                onChange={e => setForm(f => ({ ...f, resolutionTime: e.target.value }))}
                disabled={isLocked}
                {...focusHandlers(!!resolutionTimeHint)}
              />
            </Field>
          </div>
        </div>

        {/* Section: Resolution */}
        <div style={sectionCard}>
          <h2 style={sectionTitle}>Resolution</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            <Field id="criteria" label="Resolution criteria *" hint={criteriaHint}>
              <textarea
                id="criteria"
                style={textareaStyle(!!criteriaHint)}
                value={form.criteria}
                onChange={e => setForm(f => ({ ...f, criteria: e.target.value }))}
                placeholder="Exactly what determines the outcome? e.g. Resolves YES if the MAS print for December 2026, as first published, is strictly below 2.0%."
                disabled={isLocked}
                {...textareaFocus(!!criteriaHint)}
              />
            </Field>

            <div>
              <p style={{ fontSize: 13, fontWeight: 600, color: NAV, margin: '0 0 8px' }}>
                Resolution sources *
              </p>
              {resolutionSourcesHint && (
                <p style={{ fontSize: 12, color: '#9ca3af', margin: '-4px 0 12px' }}>
                  {resolutionSourcesHint}
                </p>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {form.sources.map((src, i) => {
                  const urlHint = getHint(`resolution_sources[${i}].url`)
                  return (
                    <div key={src.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                      <div style={{ flex: 2 }}>
                        <Field id={`src-url-${i}`} label={i === 0 ? 'URL' : ' '} hint={urlHint}>
                          <input
                            id={`src-url-${i}`}
                            style={inputBase(!!urlHint)}
                            value={src.url}
                            onChange={e => updateSource(src.id, 'url', e.target.value)}
                            placeholder="https://…"
                            disabled={isLocked}
                            {...focusHandlers(!!urlHint)}
                          />
                        </Field>
                      </div>
                      <div style={{ flex: 1 }}>
                        <Field id={`src-label-${i}`} label={i === 0 ? 'Label (optional)' : ' '}>
                          <input
                            id={`src-label-${i}`}
                            style={inputBase(false)}
                            value={src.label}
                            onChange={e => updateSource(src.id, 'label', e.target.value)}
                            placeholder="e.g. MAS statistics"
                            disabled={isLocked}
                            {...focusHandlers(false)}
                          />
                        </Field>
                      </div>
                      {form.sources.length > 1 && !isLocked && (
                        <button
                          type="button"
                          onClick={() => removeSource(src.id)}
                          aria-label={`Remove source ${i + 1}`}
                          style={{ marginTop: i === 0 ? 22 : 0, lineHeight: '50px', background: 'none', border: 'none', color: '#dc2626', cursor: 'pointer', fontSize: 20 }}
                        >
                          ×
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
              {!isLocked && (
                <button type="button" onClick={addSource} style={ghostBtn}>
                  + Add source
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Section: Pricing */}
        <div style={sectionCard}>
          <h2 style={sectionTitle}>Pricing</h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <Field id="seed-subsidy" label="Seed subsidy (credits) *" hint={seedSubsidyHint}>
              <input
                id="seed-subsidy"
                type="number"
                min={0}
                step="0.01"
                style={inputBase(!!seedSubsidyHint)}
                value={form.seedSubsidy}
                onChange={e => setForm(f => ({ ...f, seedSubsidy: e.target.value }))}
                placeholder="e.g. 250"
                disabled={isLocked}
                {...focusHandlers(!!seedSubsidyHint)}
              />
            </Field>
            <Field
              id="liquidity-b"
              label="Liquidity parameter b"
              hint={liquidityBHint ?? 'Leave blank to use the server default'}
            >
              <input
                id="liquidity-b"
                type="number"
                min={0}
                step="0.01"
                style={inputBase(!!liquidityBHint)}
                value={form.liquidityB}
                onChange={e => setForm(f => ({ ...f, liquidityB: e.target.value }))}
                placeholder="Default: 100"
                disabled={isLocked}
                {...focusHandlers(!!liquidityBHint)}
              />
            </Field>
          </div>
          {lastSave?.market.max_platform_loss != null && (
            <div
              aria-label="max platform loss"
              style={{ marginTop: 16, padding: '12px 16px', backgroundColor: CREAM, borderRadius: 10, fontSize: 13, color: '#6b7280' }}
            >
              Max platform loss:{' '}
              <strong style={{ color: NAV }}>{lastSave.market.max_platform_loss.toFixed(4)} credits</strong>
              {seedSubsidyNum != null && (
                <span style={{
                  marginLeft: 12,
                  fontWeight: 600,
                  color: seedSubsidyNum >= lastSave.market.max_platform_loss ? '#16a34a' : '#d97706',
                }}>
                  {seedSubsidyNum >= lastSave.market.max_platform_loss
                    ? '✓ Seed subsidy covers worst case'
                    : '⚠ Seed subsidy below max loss'}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start', paddingBottom: 48 }}>
          {submitError && (
            <p role="alert" style={{ fontSize: 13, color: '#dc2626', margin: 0 }}>
              {submitError}
            </p>
          )}

          {!isLocked && (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting || (lastSave?.blocking_submission.length ?? 0) > 0}
              style={{
                backgroundColor: NAV,
                color: '#fff',
                border: 'none',
                borderRadius: 10,
                padding: '14px 32px',
                fontSize: 15,
                fontWeight: 600,
                cursor: submitting || (lastSave?.blocking_submission.length ?? 0) > 0 ? 'default' : 'pointer',
                opacity: submitting || (lastSave?.blocking_submission.length ?? 0) > 0 ? 0.6 : 1,
              }}
            >
              {submitting ? 'Submitting…' : 'Submit for Review'}
            </button>
          )}

          {isSubmitted && (
            <>
              {publishError && (
                <p role="alert" style={{ fontSize: 13, color: '#dc2626', margin: 0 }}>
                  {publishError}
                </p>
              )}
              <div style={{ fontSize: 13, color: '#6b7280', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{
                  display: 'inline-block',
                  padding: '3px 12px',
                  borderRadius: 20,
                  backgroundColor: 'rgba(217,119,6,0.1)',
                  color: '#d97706',
                  fontWeight: 700,
                  fontSize: 11,
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                }}>
                  Submitted
                </span>
                Market is ready to be published.
              </div>
              <button
                type="button"
                onClick={handlePublish}
                disabled={publishing}
                style={{
                  backgroundColor: GOLD,
                  color: '#fff',
                  border: 'none',
                  borderRadius: 10,
                  padding: '14px 32px',
                  fontSize: 15,
                  fontWeight: 600,
                  cursor: publishing ? 'default' : 'pointer',
                  opacity: publishing ? 0.6 : 1,
                }}
              >
                {publishing ? 'Publishing…' : 'Publish Market'}
              </button>
            </>
          )}
        </div>
      </main>
    </div>
  )
}
