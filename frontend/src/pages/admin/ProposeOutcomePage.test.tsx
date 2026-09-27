import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext } from '../../context/AuthContext'
import type { User } from '../../context/AuthContext'
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

function renderPage(id = 'mkt-1') {
  return render(
    <AuthContext.Provider value={{ user: admin, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={[`/admin/markets/${id}/propose-outcome`]}>
        <Routes>
          <Route path="/admin/markets/:id/propose-outcome" element={<ProposeOutcomePage />} />
          <Route path="/admin/markets" element={<p>markets list</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
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
    mockGet(baseMarket({ status: 'open' }))
    renderPage()
    expect(await screen.findByText(/has not finished yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /propose outcome/i })).not.toBeInTheDocument()
  })

  it('shows a friendly message when a proposal is already pending', async () => {
    mockGet(baseMarket({ status: 'pending_resolution' }))
    renderPage()
    expect(await screen.findByText(/already been proposed/i)).toBeInTheDocument()
  })

  it('shows a friendly message when the market is already approved', async () => {
    mockGet(baseMarket({ status: 'approved' }))
    renderPage()
    expect(await screen.findByText(/already been approved/i)).toBeInTheDocument()
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

  it('shows a reload message on 409 market_pending_resolution from a race', async () => {
    mockGet(baseMarket())
    server.use(
      http.post(`${MARKET_BASE}/markets/mkt-1/propose-outcome`, () =>
        HttpResponse.json({ error: { code: 'market_pending_resolution', message: 'Already proposed.' } }, { status: 409 }),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will Singapore core inflation be below 2%?')

    await actor.click(screen.getByRole('button', { name: 'Yes' }))
    await actor.type(screen.getByLabelText('Written note'), 'MAS published 1.8% for December.')
    await actor.click(screen.getByRole('button', { name: /propose outcome/i }))

    expect(await screen.findByText(/already been proposed/i)).toBeInTheDocument()
  })

  it('shows an error when the market fails to load', async () => {
    server.use(http.get(`${MARKET_BASE}/markets/mkt-1`, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })
})
