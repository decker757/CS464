import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../../context/AuthContext'
import { holdUntilReleased } from '../../test/holdUntilReleased'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import CreateMarketPage from './CreateMarketPage'

const MARKET_BASE = 'http://localhost:8001'

const admin: User = {
  id: 'u1',
  username: 'admin1',
  email: 'admin@smu.edu.sg',
  role: 'admin',
  created_at: '2026-01-01',
}

const baseSaveResponse = {
  market: {
    id: 'mkt-123',
    status: 'draft',
    question: null,
    description: null,
    outcomes: [
      { id: 'o1', position: 0, label: 'Yes', initial_price: 0.5 },
      { id: 'o2', position: 1, label: 'No', initial_price: 0.5 },
    ],
    close_time: null,
    resolution_time: null,
    resolution_criteria: null,
    resolution_sources: [],
    liquidity_b: 100,
    seed_subsidy: null,
    max_platform_loss: 69.3147,
  },
  blocking_submission: [] as { field: string; message: string }[],
}

// Autosave only sends a form that differs from what was last saved, so a test
// that wants a save has to change something first.
function touchForm() {
  fireEvent.change(screen.getByLabelText('Question *'), { target: { value: 'Will it rain in Singapore tomorrow?' } })
}

function renderPage() {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={['/admin/markets/new']}>
        <Routes>
          <Route path="/admin/markets/new" element={<CreateMarketPage />} />
          <Route path="/markets" element={<p>markets list</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

// --- the same page on /admin/markets/:id/edit (#348) ------------------------

const STORED_ID = 'mkt-stored'
const STORED_DRAFT_KEY = '11111111-2222-3333-4444-555555555555'
const STORED_QUESTION = 'Will the resumed draft keep everything it was given?'
// Built from local parts, so the value a datetime-local input is expected to
// hold does not depend on the machine's timezone.
const CLOSE_AT = new Date(2027, 0, 15, 14, 30)
const RESOLVE_AT = new Date(2027, 0, 20, 9, 5)

const storedMarket = {
  id: STORED_ID,
  draft_key: STORED_DRAFT_KEY,
  status: 'draft',
  question: STORED_QUESTION,
  description: 'Context a trader reads before buying.',
  outcomes: [
    { id: 'so1', position: 0, label: 'Rain', initial_price: 0.5 },
    { id: 'so2', position: 1, label: 'No rain', initial_price: 0.5 },
  ],
  close_time: CLOSE_AT.toISOString(),
  resolution_time: RESOLVE_AT.toISOString(),
  resolution_criteria: 'Resolves YES if the MAS print for December 2026 is below 2.0%.',
  resolution_sources: [{ id: 'ss1', position: 0, url: 'https://example.com/mas', label: 'MAS statistics' }],
  liquidity_b: 150,
  seed_subsidy: 250,
  max_platform_loss: 103.9721,
}

function serveStoredMarket(overrides: Record<string, unknown> = {}) {
  server.use(
    http.get(`${MARKET_BASE}/markets/${STORED_ID}`, () =>
      HttpResponse.json({ ...storedMarket, ...overrides }),
    ),
  )
}

function renderEditPage() {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={[`/admin/markets/${STORED_ID}/edit`]}>
        <Routes>
          <Route path="/admin/markets/:id/edit" element={<CreateMarketPage />} />
          <Route path="/markets" element={<p>markets list</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('CreateMarketPage', () => {
  beforeEach(() => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, () =>
        HttpResponse.json(baseSaveResponse, { status: 201 }),
      ),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders all major form sections', () => {
    renderPage()
    expect(screen.getByText('New Market')).toBeInTheDocument()
    expect(screen.getByText('Question')).toBeInTheDocument()
    expect(screen.getByText('Outcomes')).toBeInTheDocument()
    expect(screen.getByText('Timeline')).toBeInTheDocument()
    expect(screen.getByText('Resolution')).toBeInTheDocument()
    expect(screen.getByText('Pricing')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /submit for review/i })).toBeInTheDocument()
  })

  it('starts with two default outcome fields', () => {
    renderPage()
    expect(screen.getByDisplayValue('Yes')).toBeInTheDocument()
    expect(screen.getByDisplayValue('No')).toBeInTheDocument()
  })

  // The indicator is a live region and carries the time, so "Saved" does not
  // read the same three seconds and ten minutes later (#363).
  it('autosave fires after 3 seconds and announces the time it saved', async () => {
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByRole('status')).toHaveTextContent(/^Saved at \d{1,2}:\d{2}/)
  })

  it('offers a retry after a failed save, and saves when it is pressed', async () => {
    let calls = 0
    server.use(
      http.post(`${MARKET_BASE}/markets`, () => {
        calls++
        return calls === 1 ? HttpResponse.error() : HttpResponse.json(baseSaveResponse)
      }),
    )
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByRole('status')).toHaveTextContent('Save failed')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /retry/i })) })
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByRole('status')).toHaveTextContent(/^Saved at \d{1,2}:\d{2}/)
  })

  it('shows initial_price beside outcomes after first autosave', async () => {
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    const prices = screen.getAllByText('50.0% start')
    expect(prices.length).toBe(2)
  })

  it('shows max_platform_loss from server response', async () => {
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByLabelText('max platform loss')).toHaveTextContent('69.3147')
  })

  it('shows blocking_submission hints on the relevant fields', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, () =>
        HttpResponse.json({
          ...baseSaveResponse,
          blocking_submission: [
            { field: 'close_time', message: 'A close time is required.' },
            { field: 'seed_subsidy', message: 'A seed subsidy is required.' },
          ],
        }),
      ),
    )
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByText('A close time is required.')).toBeInTheDocument()
    expect(screen.getByText('A seed subsidy is required.')).toBeInTheDocument()
  })

  it('marks a text area invalid when a blocking hint names it', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, () =>
        HttpResponse.json({
          ...baseSaveResponse,
          blocking_submission: [{ field: 'resolution_criteria', message: 'Resolution criteria are required.' }],
        }),
      ),
    )
    vi.useFakeTimers()
    renderPage()
    expect(screen.getByLabelText('Resolution criteria *')).toBeValid()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByLabelText('Resolution criteria *')).toBeInvalid()
  })

  it('does not save a form nobody has touched', async () => {
    let calls = 0
    server.use(http.post(`${MARKET_BASE}/markets`, () => { calls++; return HttpResponse.json(baseSaveResponse) }))
    vi.useFakeTimers()
    renderPage()
    await act(() => vi.advanceTimersByTimeAsync(9100))
    expect(calls).toBe(0)
  })

  it('does not autosave again until something changes', async () => {
    let calls = 0
    server.use(http.post(`${MARKET_BASE}/markets`, () => { calls++; return HttpResponse.json(baseSaveResponse) }))
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(9100))
    expect(calls).toBe(1)
    fireEvent.change(screen.getByLabelText('Question *'), { target: { value: 'Will it rain in Singapore on Friday?' } })
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(calls).toBe(2)
  })

  it('shows a source hint under the row it is about when an earlier row is blank', async () => {
    // Blank URLs are not sent, so the server's resolution_sources[0] is the
    // second row on screen here.
    server.use(
      http.post(`${MARKET_BASE}/markets`, () =>
        HttpResponse.json({
          ...baseSaveResponse,
          blocking_submission: [{ field: 'resolution_sources[0].url', message: 'Use an http or https link.' }],
        }),
      ),
    )
    vi.useFakeTimers()
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /add source/i }))
    fireEvent.change(document.getElementById('src-url-1')!, { target: { value: 'ftp://example.com' } })
    await act(() => vi.advanceTimersByTimeAsync(3100))
    const second = document.getElementById('src-url-1')!.parentElement!
    expect(within(second).getByText('Use an http or https link.')).toBeInTheDocument()
    const first = document.getElementById('src-url-0')!.parentElement!
    expect(within(first).queryByText('Use an http or https link.')).not.toBeInTheDocument()
  })

  it('does not send a zero seed subsidy, says why, and blocks submit', async () => {
    const bodies: Record<string, unknown>[] = []
    server.use(
      http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
        bodies.push(await request.json() as Record<string, unknown>)
        return HttpResponse.json(baseSaveResponse)
      }),
    )
    vi.useFakeTimers()
    renderPage()
    touchForm()
    fireEvent.change(screen.getByLabelText(/seed subsidy/i), { target: { value: '0' } })
    expect(screen.getByText('Must be greater than zero.')).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(bodies).toHaveLength(1)
    expect(bodies[0]).not.toHaveProperty('seed_subsidy')
    expect(screen.getByRole('button', { name: /submit for review/i })).toBeDisabled()
  })

  it('can add and remove outcome rows', async () => {
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /add outcome/i }))
    expect(screen.getByLabelText('Outcome 3')).toBeInTheDocument()
    await actor.click(screen.getByRole('button', { name: /remove outcome 3/i }))
    expect(screen.queryByLabelText('Outcome 3')).not.toBeInTheDocument()
  })

  it('calls POST /markets with status submitted when submit is clicked', async () => {
    const calls: string[] = []
    server.use(
      http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
        const body = await request.json() as { status: string }
        calls.push(body.status)
        return HttpResponse.json({
          ...baseSaveResponse,
          market: { ...baseSaveResponse.market, status: body.status },
        })
      }),
    )
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /submit for review/i }))
    await waitFor(() => expect(calls).toContain('submitted'))
  })

  it('shows publish button after successful submission', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
        const body = await request.json() as { status: string }
        return HttpResponse.json({
          ...baseSaveResponse,
          market: { ...baseSaveResponse.market, status: body.status },
        })
      }),
    )
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /submit for review/i }))
    expect(await screen.findByRole('button', { name: /publish market/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /submit for review/i })).not.toBeInTheDocument()
  })

  it('navigates to /markets after publish succeeds', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
        const body = await request.json() as { status: string }
        return HttpResponse.json({
          ...baseSaveResponse,
          market: { ...baseSaveResponse.market, status: body.status },
        })
      }),
      http.post(`${MARKET_BASE}/markets/:id/publish`, () =>
        HttpResponse.json({ ...baseSaveResponse.market, status: 'open' }),
      ),
    )
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /submit for review/i }))
    await actor.click(await screen.findByRole('button', { name: /publish market/i }))
    expect(await screen.findByText('markets list')).toBeInTheDocument()
  })

  it('shows an error and re-enables the button when publish fails', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
        const body = await request.json() as { status: string }
        return HttpResponse.json({
          ...baseSaveResponse,
          market: { ...baseSaveResponse.market, status: body.status },
        })
      }),
      http.post(`${MARKET_BASE}/markets/:id/publish`, () => HttpResponse.error()),
    )
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /submit for review/i }))
    await actor.click(await screen.findByRole('button', { name: /publish market/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/publish failed/i)
    expect(screen.getByRole('button', { name: /publish market/i })).toBeInTheDocument()
  })

  // Another session moved the market past draft; the form must stop saving and lock.
  it.each([
    'market_not_editable',
    'market_already_open',
    'market_closed',
    'market_pending_resolution',
    'market_already_approved',
  ])(
    'locks the form when autosave is refused with %s',
    async (code) => {
      server.use(
        http.post(`${MARKET_BASE}/markets`, () =>
          HttpResponse.json({ error: { code, message: 'refused' } }, { status: 409 }),
        ),
      )
      vi.useFakeTimers()
      renderPage()
      touchForm()
      await act(() => vi.advanceTimersByTimeAsync(3100))
      expect(screen.getByLabelText('Question *')).toBeDisabled()
      expect(screen.queryByRole('button', { name: /submit for review/i })).not.toBeInTheDocument()
      // The lock used to be silent, which is the bug #363 fixes: the form
      // greyed out and said nothing at all.
      expect(screen.getByRole('alert')).toHaveTextContent(/read-only/i)
      expect(screen.queryByText('Save failed')).not.toBeInTheDocument()
    },
  )

  it('says the market was published, that the open edits were lost, and links to it', async () => {
    let calls = 0
    server.use(
      http.post(`${MARKET_BASE}/markets`, () => {
        calls++
        return calls === 1
          ? HttpResponse.json(baseSaveResponse, { status: 201 })
          : HttpResponse.json({ error: { code: 'market_already_open', message: 'refused' } }, { status: 409 })
      }),
    )
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    fireEvent.change(screen.getByLabelText('Question *'), { target: { value: 'Will it rain on Friday?' } })
    await act(() => vi.advanceTimersByTimeAsync(3100))
    const banner = screen.getByRole('alert')
    expect(banner).toHaveTextContent(/already been published/i)
    expect(banner).toHaveTextContent(/were not saved/i)
    expect(within(banner).getByRole('link', { name: /view the market/i })).toHaveAttribute('href', '/markets/mkt-123')
  })

  it('says so when the lock-out cost no edits', async () => {
    let calls = 0
    server.use(
      http.post(`${MARKET_BASE}/markets`, () => {
        calls++
        return calls === 1
          ? HttpResponse.json(baseSaveResponse, { status: 201 })
          : HttpResponse.json({ error: { code: 'market_already_open', message: 'refused' } }, { status: 409 })
      }),
    )
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    // Submitting an unchanged form: the refusal costs nothing that was typed.
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /submit for review/i })) })
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByRole('alert')).toHaveTextContent(/had already been saved/i)
  })

  // Nothing disables the fields until the answer arrives, so a submit can be
  // typed over while it is in flight. Comparing what the request set out with
  // would call that typing saved.
  it('counts edits made while the submit was in flight as lost', async () => {
    let calls = 0
    let releaseSubmit = () => {}
    const submitAnswered = new Promise<void>(resolve => { releaseSubmit = resolve })
    server.use(
      http.post(`${MARKET_BASE}/markets`, async () => {
        calls++
        if (calls === 1) return HttpResponse.json(baseSaveResponse, { status: 201 })
        await submitAnswered
        return HttpResponse.json({ error: { code: 'market_already_open', message: 'refused' } }, { status: 409 })
      }),
    )
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /submit for review/i })) })
    fireEvent.change(screen.getByLabelText('Resolution criteria *'), { target: { value: 'One more sentence.' } })
    releaseSubmit()
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByRole('alert')).toHaveTextContent(/were not saved/i)
  })

  it('clears a refused submission reason once the field is fixed', async () => {
    let calls = 0
    server.use(
      http.post(`${MARKET_BASE}/markets`, () => {
        calls++
        return calls === 1
          ? HttpResponse.json(
            {
              error: {
                code: 'draft_incomplete',
                message: 'The market is not ready.',
                details: [{ field: 'close_time', message: 'The close time has already passed.' }],
              },
            },
            { status: 422 },
          )
          : HttpResponse.json(baseSaveResponse)
      }),
    )
    vi.useFakeTimers()
    renderPage()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /submit for review/i })) })
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(screen.getByText('The close time has already passed.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Closes at *'), { target: { value: '2027-01-01T12:00' } })
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.queryByText('The close time has already passed.')).not.toBeInTheDocument()
  })

  it('offers Publish when autosave learns the market was already submitted', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, () =>
        HttpResponse.json({ error: { code: 'market_not_editable', message: 'refused' } }, { status: 409 }),
      ),
    )
    vi.useFakeTimers()
    renderPage()
    touchForm()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByRole('button', { name: /publish market/i })).toBeInTheDocument()
  })

  it('shows why publish was refused under the field it is about', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
        const body = await request.json() as { status: string }
        return HttpResponse.json({
          ...baseSaveResponse,
          market: { ...baseSaveResponse.market, status: body.status },
        })
      }),
      http.post(`${MARKET_BASE}/markets/:id/publish`, () =>
        HttpResponse.json(
          {
            error: {
              code: 'draft_incomplete',
              message: 'The market is not ready.',
              details: [{ field: 'close_time', message: 'The close time has already passed.' }],
            },
          },
          { status: 422 },
        ),
      ),
    )
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /submit for review/i }))
    await actor.click(await screen.findByRole('button', { name: /publish market/i }))
    const closeTimeField = (await screen.findByLabelText('Closes at *')).parentElement!
    expect(within(closeTimeField).getByText('The close time has already passed.')).toBeInTheDocument()
  })

  // Publishing re-runs every submission rule against the clock (ADR 0008), so
  // a market that sat submitted past its close time fails forever. Telling the
  // admin to try again is wrong.
  it('says a publish refused for draft_incomplete cannot be retried', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
        const body = await request.json() as { status: string }
        return HttpResponse.json({
          ...baseSaveResponse,
          market: { ...baseSaveResponse.market, status: body.status },
        })
      }),
      http.post(`${MARKET_BASE}/markets/:id/publish`, () =>
        HttpResponse.json(
          { error: { code: 'draft_incomplete', message: 'The market is not ready.' } },
          { status: 422 },
        ),
      ),
    )
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /submit for review/i }))
    await actor.click(await screen.findByRole('button', { name: /publish market/i }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/cannot edit a market once it has been submitted/i)
    expect(alert).not.toHaveTextContent(/try again/i)
  })

  it('shows the server reasons when a submission is refused', async () => {
    server.use(
      http.post(`${MARKET_BASE}/markets`, () =>
        HttpResponse.json(
          {
            error: {
              code: 'draft_incomplete',
              message: 'The market is not ready.',
              details: [{ field: 'close_time', message: 'A close time is required.' }],
            },
          },
          { status: 422 },
        ),
      ),
    )
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /submit for review/i }))
    const closeTimeField = (await screen.findByLabelText('Closes at *')).parentElement!
    expect(within(closeTimeField).getByText('A close time is required.')).toBeInTheDocument()
  })

  it('names every resolution-source row in the accessibility tree', async () => {
    const actor = userEvent.setup({ delay: null })
    renderPage()
    await actor.click(screen.getByRole('button', { name: /add source/i }))
    expect(screen.getByLabelText('Source 2 URL')).toBeInTheDocument()
    expect(screen.getByLabelText('Source 2 label (optional)')).toBeInTheDocument()
  })

  it('goes back to the admin market list, not the trader one', () => {
    renderPage()
    // The navbar's own "All Markets" link matches too, so name the back link exactly.
    expect(screen.getByRole('link', { name: '← All Markets' })).toHaveAttribute('href', '/admin/markets')
  })

  // #348. Nothing used to load an existing market back into this form, so a
  // market submitted in one sitting became unreachable after a reload and an
  // abandoned draft was duplicated by the next visit's fresh draft_key.
  describe('on an existing market', () => {
    it('loads the market into the form', async () => {
      serveStoredMarket()
      renderEditPage()

      expect(await screen.findByDisplayValue(STORED_QUESTION)).toBeInTheDocument()
      expect(screen.getByLabelText('Description (optional)')).toHaveValue('Context a trader reads before buying.')
      expect(screen.getByLabelText('Outcome 1')).toHaveValue('Rain')
      expect(screen.getByLabelText('Outcome 2')).toHaveValue('No rain')
      expect(screen.getByLabelText('Closes at *')).toHaveValue('2027-01-15T14:30')
      expect(screen.getByLabelText('Resolves by *')).toHaveValue('2027-01-20T09:05')
      expect(screen.getByLabelText('Resolution criteria *')).toHaveValue(storedMarket.resolution_criteria)
      expect(screen.getByLabelText('Source 1 URL')).toHaveValue('https://example.com/mas')
      expect(screen.getByLabelText('Source 1 label (optional)')).toHaveValue('MAS statistics')
      expect(screen.getByLabelText('Seed subsidy (credits) *')).toHaveValue(250)
      expect(screen.getByLabelText('Liquidity parameter b')).toHaveValue(150)
    })

    // The duplicate half of the bug: the next autosave has to continue the
    // market it just read, which means sending that market's own draft_key
    // rather than a uuid minted on this mount.
    it('resumes the market under its stored draft_key, and does not re-save what it just read', async () => {
      const keysSent: string[] = []
      serveStoredMarket()
      server.use(
        http.post(`${MARKET_BASE}/markets`, async ({ request }) => {
          const body = await request.json() as { draft_key: string }
          keysSent.push(body.draft_key)
          return HttpResponse.json(baseSaveResponse)
        }),
      )
      vi.useFakeTimers()
      renderEditPage()

      await act(() => vi.advanceTimersByTimeAsync(3100))
      expect(screen.getByDisplayValue(STORED_QUESTION)).toBeInTheDocument()

      // A whole autosave interval after the market landed, with nothing typed:
      // what the server just handed over has nothing new to say back to it.
      await act(() => vi.advanceTimersByTimeAsync(3100))
      expect(keysSent).toEqual([])

      fireEvent.change(screen.getByLabelText('Question *'), { target: { value: 'Will the resumed draft be renamed?' } })
      await act(() => vi.advanceTimersByTimeAsync(3100))

      expect(keysSent).toEqual([STORED_DRAFT_KEY])
    })

    // The ticket's second criterion. The button used to render only from the
    // save response this session happened to hold.
    it('offers Publish on a submitted market, with the form read-only and no edits to account for', async () => {
      serveStoredMarket({ status: 'submitted' })
      renderEditPage()

      expect(await screen.findByRole('button', { name: /publish market/i })).toBeInTheDocument()
      expect(screen.getByLabelText('Question *')).toBeDisabled()
      expect(screen.queryByRole('button', { name: /submit for review/i })).not.toBeInTheDocument()
      const banner = screen.getByRole('alert')
      expect(banner).toHaveTextContent(/already been submitted for review/i)
      expect(banner).toHaveTextContent(/read-only/i)
      // Nothing was being typed here, so neither sentence about open edits belongs.
      expect(banner).not.toHaveTextContent(/were not saved|had already been saved/i)
    })

    it('publishes the market it loaded', async () => {
      serveStoredMarket({ status: 'submitted' })
      server.use(http.post(`${MARKET_BASE}/markets/${STORED_ID}/publish`, () => HttpResponse.json({ status: 'open' })))
      const actor = userEvent.setup()
      renderEditPage()

      await actor.click(await screen.findByRole('button', { name: /publish market/i }))

      expect(await screen.findByText('markets list')).toBeInTheDocument()
    })

    it('locks the form and says why for a market that is already published', async () => {
      serveStoredMarket({ status: 'open' })
      renderEditPage()

      const banner = await screen.findByRole('alert')
      expect(banner).toHaveTextContent(/already been published/i)
      expect(screen.queryByRole('button', { name: /submit for review/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /publish market/i })).not.toBeInTheDocument()
      expect(within(banner).getByRole('link', { name: /view the market/i })).toHaveAttribute('href', `/markets/${STORED_ID}`)
    })

    // A form shown before the answer arrives is a form whose typing hydration
    // then throws away, and whose autosave would create the second market.
    it('offers no form while the market is still on its way', async () => {
      const { held, release } = holdUntilReleased()
      server.use(
        http.get(`${MARKET_BASE}/markets/${STORED_ID}`, async () => {
          await held
          return HttpResponse.json(storedMarket)
        }),
      )
      renderEditPage()

      expect(screen.queryByLabelText('Question *')).not.toBeInTheDocument()

      release()
      expect(await screen.findByDisplayValue(STORED_QUESTION)).toBeInTheDocument()
    })

    it('says so when the market cannot be loaded', async () => {
      server.use(
        http.get(`${MARKET_BASE}/markets/${STORED_ID}`, () =>
          HttpResponse.json({ error: { code: 'market_not_found', message: 'not yours' } }, { status: 404 }),
        ),
      )
      renderEditPage()

      expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load this market/i)
      expect(screen.queryByLabelText('Question *')).not.toBeInTheDocument()
    })
  })
})
