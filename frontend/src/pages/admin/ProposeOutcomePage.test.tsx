import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import ProposeOutcomePage from './ProposeOutcomePage'

const MARKET_BASE = 'http://localhost:8001'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

function baseMarket(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'mkt-1',
    status: 'closed',
    question: 'Will Singapore core inflation be below 2%?',
    description: null,
    outcomes: [
      { id: 'o-yes', position: 0, label: 'Yes', initial_price: 0.5 },
      { id: 'o-no', position: 1, label: 'No', initial_price: 0.5 },
    ],
    close_time: '2025-01-01T00:00:00Z',
    resolution_time: '2025-01-10T00:00:00Z',
    resolution_criteria: 'Resolves YES if below 2%.',
    resolution_sources: [],
    liquidity_b: 100,
    seed_subsidy: 250,
    max_platform_loss: 69.31,
    proposal_id: null,
    proposed_outcome_id: null,
    proposed_by_id: null,
    proposed_by_username: null,
    proposed_at: null,
    proposal_evidence_url: null,
    proposal_evidence_note: null,
    approved_by_id: null,
    approved_by_username: null,
    approved_at: null,
    ...overrides,
  }
}

const PENDING = {
  status: 'pending_resolution',
  proposed_outcome_id: 'o-yes',
  proposed_by_id: 'u2',
  proposed_by_username: 'admin2',
  proposed_at: '2025-01-02T09:00:00Z',
}

const APPROVED = {
  ...PENDING,
  status: 'approved',
  approved_by_id: 'u3',
  approved_by_username: 'admin3',
  approved_at: '2025-01-03T09:00:00Z',
}

function renderPage(id = 'mkt-1') {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={[`/admin/markets/${id}/propose-outcome`]}>
        <Routes>
          <Route path="/admin/markets/:id/propose-outcome" element={<ProposeOutcomePage />} />
          <Route path="/admin/markets" element={<p>markets list</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

function mockGet(market: ReturnType<typeof baseMarket>) {
  server.use(http.get(`${MARKET_BASE}/markets/mkt-1`, () => HttpResponse.json(market)))
}

describe('ProposeOutcomePage', () => {
  it('shows the market question and each outcome as a selectable option', async () => {
    mockGet(baseMarket())
    renderPage()
    expect(await screen.findByText('Will Singapore core inflation be below 2%?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Yes' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'No' })).toBeInTheDocument()
  })

  it('disables submit until an outcome is picked and evidence is given', async () => {
    mockGet(baseMarket())
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2%?')

    const submit = screen.getByRole('button', { name: /propose outcome/i })
    expect(submit).toBeDisabled()

    await actor.click(screen.getByRole('button', { name: 'Yes' }))
    expect(submit).toBeDisabled() // still no evidence

    await actor.type(screen.getByLabelText('Written note'), 'MAS published 1.8% for December.')
    expect(submit).toBeEnabled()
  })

  it('submits the winning outcome and evidence, then navigates to the markets list', async () => {
    mockGet(baseMarket())
    let sentBody: unknown = null
    server.use(
      http.post(`${MARKET_BASE}/markets/mkt-1/propose-outcome`, async ({ request }) => {
        sentBody = await request.json()
        return HttpResponse.json(baseMarket({ status: 'pending_resolution' }))
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2%?')

    await actor.click(screen.getByRole('button', { name: 'Yes' }))
    await actor.type(screen.getByLabelText('Source URL'), 'https://www.mas.gov.sg/statistics')
    await actor.click(screen.getByRole('button', { name: /propose outcome/i }))

    expect(await screen.findByText('markets list')).toBeInTheDocument()
    expect(sentBody).toEqual({
      winning_outcome_id: 'o-yes',
      evidence_url: 'https://www.mas.gov.sg/statistics',
      evidence_note: undefined,
    })
  })

  it('shows a friendly message and hides the form when the market has not closed yet', async () => {
    mockGet(baseMarket({ status: 'open', close_time: '2099-01-01T00:00:00Z' }))
    renderPage()
    expect(await screen.findByText(/has not finished yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /propose outcome/i })).not.toBeInTheDocument()
  })

  it('says to reload shortly when the close time has passed but the market still reads open', async () => {
    // The markets list already shows it closed; the sweep has not written it yet.
    mockGet(baseMarket({ status: 'open', close_time: '2025-01-01T00:00:00Z' }))
    renderPage()
    expect(await screen.findByText(/reload the page shortly/i)).toBeInTheDocument()
    expect(screen.queryByText(/has not finished yet/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /propose outcome/i })).not.toBeInTheDocument()
  })

  it('shows who proposed, instead of the form, when a proposal is already pending', async () => {
    mockGet(baseMarket(PENDING))
    renderPage()
    expect(await screen.findByText(/awaiting approval: proposed by admin2 on .*2025/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /propose outcome/i })).not.toBeInTheDocument()
  })

  it('shows who approved, instead of the form, when the market is already approved', async () => {
    mockGet(baseMarket(APPROVED))
    renderPage()
    expect(await screen.findByText(/approved by admin3 on .*2025/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /propose outcome/i })).not.toBeInTheDocument()
  })

  it('shows field errors from a 422 without losing the form', async () => {
    mockGet(baseMarket())
    server.use(
      http.post(`${MARKET_BASE}/markets/mkt-1/propose-outcome`, () =>
        HttpResponse.json(
          {
            error: {
              code: 'proposal_incomplete',
              message: 'This outcome cannot be proposed yet.',
              details: [
                { field: 'evidence_url', message: 'Enter a full http or https address a trader can open.' },
              ],
            },
          },
          { status: 422 },
        ),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2%?')

    await actor.click(screen.getByRole('button', { name: 'Yes' }))
    await actor.type(screen.getByLabelText('Source URL'), 'not-a-url')
    await actor.click(screen.getByRole('button', { name: /propose outcome/i }))

    expect(await screen.findByText(/enter a full http or https address/i)).toBeInTheDocument()
    expect(screen.getByText('Will Singapore core inflation be below 2%?')).toBeInTheDocument()
  })

  it.each([
    ['market_pending_resolution', PENDING, /awaiting approval: proposed by admin2 on .*2025/i],
    ['market_already_approved', APPROVED, /approved by admin3 on .*2025/i],
  ])('reloads and shows who got there first on a 409 %s', async (code, decided, expected) => {
    // The first load finds the market closed; by the time the admin submits,
    // another tab or administrator has moved it on.
    let loads = 0
    server.use(
      http.get(`${MARKET_BASE}/markets/mkt-1`, () => {
        loads++
        return HttpResponse.json(loads === 1 ? baseMarket() : baseMarket(decided))
      }),
      http.post(`${MARKET_BASE}/markets/mkt-1/propose-outcome`, () =>
        HttpResponse.json({ error: { code, message: 'Somebody got there first.' } }, { status: 409 }),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2%?')

    await actor.click(screen.getByRole('button', { name: 'Yes' }))
    await actor.type(screen.getByLabelText('Written note'), 'MAS published 1.8% for December.')
    await actor.click(screen.getByRole('button', { name: /propose outcome/i }))

    expect(await screen.findByText(expected)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /propose outcome/i })).not.toBeInTheDocument()
  })

  it('shows an error when the market fails to load', async () => {
    server.use(http.get(`${MARKET_BASE}/markets/mkt-1`, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })
})
