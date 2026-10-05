import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import ProposalsPage from './ProposalsPage'

const MARKET_BASE = 'http://localhost:8001'
const AUDIT_BASE = 'http://localhost:8002'

const admin: User = { id: 'admin-1', username: 'admin1', email: 'admin1@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

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

function mockPendingMarkets(markets: Record<string, unknown>[]) {
  server.use(http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets, next_cursor: null })))
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
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={['/admin/proposals']}>
        <Routes>
          <Route path="/admin/proposals" element={<ProposalsPage />} />
          <Route path="/admin/markets/:id/decide-outcome" element={<p>decide outcome</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('ProposalsPage', () => {
  it('shows each pending market with its proposer and winner', async () => {
    mockPendingMarkets([{ id: 'mkt-1', status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-01-01T00:00:00Z' }])
    mockAuditActions([proposedAction()])
    renderPage()
    expect(await screen.findByText('Will it rain?')).toBeInTheDocument()
    expect(screen.getByText(/proposed by ernest_t/i)).toBeInTheDocument()
    expect(screen.getByText(/winner: Yes/i)).toBeInTheDocument()
  })

  // #104 paged the browse list: a pending proposal on a later page must still be listed.
  it('shows a pending market from a later page of the list', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, ({ request }) =>
        new URL(request.url).searchParams.get('cursor') === 'page-2'
          ? HttpResponse.json({
              markets: [{ id: 'mkt-2', status: 'pending_resolution', question: 'Will it snow?', close_time: '2025-01-01T00:00:00Z' }],
              next_cursor: null,
            })
          : HttpResponse.json({
              markets: [{ id: 'mkt-1', status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-01-01T00:00:00Z' }],
              next_cursor: 'page-2',
            }),
      ),
    )
    mockAuditActions([proposedAction(), proposedAction({ id: 'a2', target_id: 'mkt-2', target_label: 'Will it snow?' })])
    renderPage()
    expect(await screen.findByText('Will it snow?')).toBeInTheDocument()
    expect(screen.getByText('Will it rain?')).toBeInTheDocument()
  })

  it('shows a Review link for another admin\'s proposal, and navigates on click', async () => {
    mockPendingMarkets([{ id: 'mkt-1', status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-01-01T00:00:00Z' }])
    mockAuditActions([proposedAction()])
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain?')

    await actor.click(screen.getByRole('link', { name: /review/i }))

    expect(await screen.findByText('decide outcome')).toBeInTheDocument()
  })

  it('shows "You proposed this" instead of Review for the signed-in admin\'s own proposal', async () => {
    mockPendingMarkets([{ id: 'mkt-1', status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-01-01T00:00:00Z' }])
    mockAuditActions([proposedAction({ actor_id: 'admin-1', actor_username: 'admin1' })])
    renderPage()
    expect(await screen.findByText(/you proposed this/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /review/i })).not.toBeInTheDocument()
  })

  it('uses the latest proposal when a market was rejected and proposed again', async () => {
    mockPendingMarkets([{ id: 'mkt-1', status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-01-01T00:00:00Z' }])
    mockAuditActions([
      proposedAction({ id: 'a1', occurred_at: '2026-01-01T00:00:00Z', context: { proposal_id: 'prop-1', winning_outcome: 'No' } }),
      proposedAction({ id: 'a2', occurred_at: '2026-01-02T00:00:00Z', context: { proposal_id: 'prop-2', winning_outcome: 'Yes' } }),
    ])
    renderPage()
    await screen.findByText('Will it rain?')
    expect(screen.getByText(/winner: Yes/i)).toBeInTheDocument()
    expect(screen.queryByText(/winner: No/i)).not.toBeInTheDocument()
  })

  it('shows an empty state when nothing is pending', async () => {
    mockPendingMarkets([])
    mockAuditActions([])
    renderPage()
    expect(await screen.findByText(/no proposals are waiting/i)).toBeInTheDocument()
  })

  it('shows an error when a request fails', async () => {
    server.use(http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.error()))
    mockAuditActions([])
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })
})
