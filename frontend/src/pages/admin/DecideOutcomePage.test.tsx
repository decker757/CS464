import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext } from '../../context/AuthContext'
import type { User } from '../../context/AuthContext'
import { server } from '../../test/server'
import DecideOutcomePage from './DecideOutcomePage'

const MARKET_BASE = 'http://localhost:8001'
const AUDIT_BASE = 'http://localhost:8002'

const admin: User = { id: 'admin-1', username: 'admin1', email: 'admin1@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

function publicMarket(overrides: Record<string, unknown> = {}) {
  return {
    id: 'mkt-1',
    status: 'pending_resolution',
    question: 'Will it rain?',
    description: null,
    outcomes: [
      { id: 'o-yes', position: 0, label: 'Yes' },
      { id: 'o-no', position: 1, label: 'No' },
    ],
    close_time: '2025-01-01T00:00:00Z',
    resolution_time: '2025-01-10T00:00:00Z',
    resolution_criteria: 'Resolves YES if it rains.',
    resolution_sources: [],
    liquidity_b: '100',
    seed_subsidy: '250',
    published_at: '2024-12-01T00:00:00Z',
    proposed_outcome_id: 'o-yes',
    ...overrides,
  }
}

function proposedAction(overrides: Record<string, unknown> = {}) {
  return {
    id: 'a1',
    occurred_at: '2026-01-01T00:00:00Z',
    actor_id: 'admin-2',
    actor_username: 'ernest_t',
    actor_role: 'admin',
    action_type: 'market.outcome_proposed',
    target_type: 'market',
    target_id: 'mkt-1',
    target_label: 'Will it rain?',
    reason: null,
    context: {
      proposal_id: 'prop-1',
      winning_outcome_id: 'o-yes',
      winning_outcome: 'Yes',
      evidence_url: 'https://example.com',
      evidence_note: 'A note.',
    },
    source_service: 'market_service',
    ...overrides,
  }
}

function mockGetMarket(market: Record<string, unknown>) {
  server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => HttpResponse.json(market)))
}

function mockAuditActions(actions: Record<string, unknown>[]) {
  server.use(
    http.get(`${AUDIT_BASE}/audit/actions`, () =>
      HttpResponse.json({ actions, next_cursor: null, has_more: false }),
    ),
  )
}

function renderPage() {
  return render(
    <AuthContext.Provider value={{ user: admin, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={['/admin/markets/mkt-1/decide-outcome']}>
        <Routes>
          <Route path="/admin/markets/:id/decide-outcome" element={<DecideOutcomePage />} />
          <Route path="/admin/proposals" element={<p>proposals list</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('DecideOutcomePage', () => {
  it('shows the proposed winner, proposer and evidence', async () => {
    mockGetMarket(publicMarket())
    mockAuditActions([proposedAction()])
    renderPage()
    expect(await screen.findByText('Will it rain?')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.getByText(/ernest_t/)).toBeInTheDocument()
    expect(screen.getByText('https://example.com')).toBeInTheDocument()
    expect(screen.getByText('A note.')).toBeInTheDocument()
  })

  it('disables Approve and Reject, with a reason, for the signed-in admin\'s own proposal', async () => {
    mockGetMarket(publicMarket())
    mockAuditActions([proposedAction({ actor_id: 'admin-1', actor_username: 'admin1' })])
    renderPage()
    await screen.findByText('Will it rain?')

    expect(screen.getByRole('button', { name: /^approve$/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^reject$/i })).toBeDisabled()
    expect(screen.getByText(/you proposed this outcome/i)).toBeInTheDocument()
  })

  it('approves and navigates to the proposals list', async () => {
    mockGetMarket(publicMarket())
    mockAuditActions([proposedAction()])
    let sentBody: unknown = null
    server.use(
      http.post(`${MARKET_BASE}/markets/mkt-1/approve-outcome`, async ({ request }) => {
        sentBody = await request.json()
        return HttpResponse.json(publicMarket({ status: 'approved' }))
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain?')

    await actor.click(screen.getByRole('button', { name: /^approve$/i }))

    expect(await screen.findByText('proposals list')).toBeInTheDocument()
    expect(sentBody).toEqual({ proposal_id: 'prop-1' })
  })

  it('opens the reject modal, requires a 10-character reason, and submits it', async () => {
    mockGetMarket(publicMarket())
    mockAuditActions([proposedAction()])
    let sentBody: unknown = null
    server.use(
      http.post(`${MARKET_BASE}/markets/mkt-1/reject-outcome`, async ({ request }) => {
        sentBody = await request.json()
        return HttpResponse.json(publicMarket({ status: 'closed' }))
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain?')

    await actor.click(screen.getByRole('button', { name: /^reject$/i }))
    const confirmButton = screen.getByRole('button', { name: /confirm rejection/i })
    expect(confirmButton).toBeDisabled()

    await actor.type(screen.getByLabelText('Reason *'), 'too short')
    expect(confirmButton).toBeDisabled() // 9 characters, still short

    await actor.type(screen.getByLabelText('Reason *'), '1')
    expect(confirmButton).toBeEnabled()

    await actor.click(confirmButton)

    expect(await screen.findByText('proposals list')).toBeInTheDocument()
    expect(sentBody).toEqual({ proposal_id: 'prop-1', reason: 'too short1' })
  })

  it('disables the controls and offers reload on a superseded proposal', async () => {
    mockGetMarket(publicMarket())
    mockAuditActions([proposedAction()])
    server.use(
      http.post(`${MARKET_BASE}/markets/mkt-1/approve-outcome`, () =>
        HttpResponse.json({ error: { code: 'proposal_superseded', message: 'Superseded.' } }, { status: 409 }),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain?')

    await actor.click(screen.getByRole('button', { name: /^approve$/i }))

    expect(await screen.findByText(/rejected and a new one made/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^approve$/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^reject$/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
  })

  it('shows a message when the market has no proposal waiting', async () => {
    mockGetMarket(publicMarket({ status: 'closed', proposed_outcome_id: null }))
    mockAuditActions([])
    renderPage()
    expect(await screen.findByText(/no proposal waiting/i)).toBeInTheDocument()
  })

  it('shows an error when the market fails to load', async () => {
    server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => HttpResponse.error()))
    mockAuditActions([])
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })
})
