import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext } from '../../context/AuthContext'
import type { User } from '../../context/AuthContext'
import { server } from '../../test/server'
import AdminMarketsPage from './AdminMarketsPage'

const MARKET_BASE = 'http://localhost:8001'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

const mockMarkets = [
  { id: 'a1', draft_key: 'k1', status: 'draft', question: 'Untitled draft', close_time: null, updated_at: '2026-01-01T00:00:00Z' },
  { id: 'b2', draft_key: 'k2', status: 'submitted', question: 'Will X happen?', close_time: '2099-03-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' },
  { id: 'c3', draft_key: 'k3', status: 'open', question: 'Will SMU win SUNIG?', close_time: '2099-01-05T12:00:00Z', updated_at: '2026-01-03T00:00:00Z' },
  { id: 'd4', draft_key: 'k4', status: 'closed', question: 'Will inflation fall below 2%?', close_time: '2025-01-01T00:00:00Z', updated_at: '2026-01-04T00:00:00Z' },
  { id: 'e5', draft_key: 'k5', status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-06-01T00:00:00Z', updated_at: '2026-01-05T00:00:00Z' },
  { id: 'f6', draft_key: 'k6', status: 'approved', question: 'Did it rain yesterday?', close_time: '2025-05-01T00:00:00Z', updated_at: '2026-01-06T00:00:00Z' },
]

function renderPage() {
  return render(
    <AuthContext.Provider value={{ user: admin, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={['/admin/markets']}>
        <Routes>
          <Route path="/admin/markets" element={<AdminMarketsPage />} />
          <Route path="/admin/markets/:id/propose-outcome" element={<p>propose outcome</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

// `markets` are the admin's own (GET /markets); `published` are every admin's
// published markets (GET /public/markets), which include the admin's own too.
function mockList(markets: typeof mockMarkets, published: Record<string, unknown>[] = []) {
  server.use(
    http.get(`${MARKET_BASE}/markets`, () => HttpResponse.json({ markets })),
    http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: published })),
  )
}

describe('AdminMarketsPage', () => {
  it('shows a row for each of the caller\'s markets', async () => {
    mockList(mockMarkets)
    renderPage()
    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
    expect(screen.getByText('Untitled draft')).toBeInTheDocument()
  })

  it('lists other administrators\' published markets beside your own, each market once', async () => {
    mockList(mockMarkets, [
      // The admin's own open market, which the public list returns as well.
      { id: 'c3', status: 'open', question: 'Will SMU win SUNIG?', close_time: '2099-01-05T12:00:00Z' },
      { id: 'x9', status: 'pending_resolution', question: 'Another admin\'s market?', close_time: '2025-07-01T00:00:00Z' },
    ])
    renderPage()
    expect(await screen.findByText('Another admin\'s market?')).toBeInTheDocument()
    expect(screen.getAllByText('Will SMU win SUNIG?')).toHaveLength(1)
    expect(screen.getByRole('tab', { name: /all/i })).toHaveTextContent('7')
    expect(screen.getAllByText(/created by you/i)).toHaveLength(6)
  })

  it('shows a count per status on the filter tabs', async () => {
    mockList(mockMarkets)
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const tabs = screen.getAllByRole('tab')
    const byName = (name: string) => tabs.find(t => within(t).queryByText(name))
    expect(byName('All')).toHaveTextContent('6')
    expect(byName('Draft')).toHaveTextContent('1')
    expect(byName('Submitted')).toHaveTextContent('1')
    expect(byName('Open')).toHaveTextContent('1')
    expect(byName('Closed')).toHaveTextContent('1')
    expect(byName('Pending')).toHaveTextContent('1')
    expect(byName('Settled')).toHaveTextContent('1')
  })

  it('sorts by soonest close time by default, with no-close-time markets last', async () => {
    mockList(mockMarkets)
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const questions = screen.getAllByText(/^(Untitled draft|Will |Did )/).map(el => el.textContent)
    // Closest close_time first: approved (2025-05), pending (2025-06), closed
    // (2025-01 -> actually earliest), open (2099-01), submitted (2099-03), then draft (no close_time) last.
    expect(questions).toEqual([
      'Will inflation fall below 2%?',
      'Did it rain yesterday?',
      'Will it rain?',
      'Will SMU win SUNIG?',
      'Will X happen?',
      'Untitled draft',
    ])
  })

  it('filters the list when a status tab is clicked', async () => {
    mockList(mockMarkets)
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(screen.getByRole('tab', { name: /open/i }))

    expect(screen.getByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.queryByText('Will inflation fall below 2%?')).not.toBeInTheDocument()
    expect(screen.queryByText('Untitled draft')).not.toBeInTheDocument()
  })

  it('shows an empty state when a filter matches no markets', async () => {
    mockList(mockMarkets.filter(m => m.status !== 'draft'))
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(screen.getByRole('tab', { name: /draft/i }))

    expect(await screen.findByText(/no markets in this status/i)).toBeInTheDocument()
  })

  it('shows an empty state when no markets are returned', async () => {
    mockList([])
    renderPage()
    expect(await screen.findByText(/no markets in this status/i)).toBeInTheDocument()
  })

  it('shows an error when the API fails', async () => {
    mockList(mockMarkets)
    server.use(http.get(`${MARKET_BASE}/markets`, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('does not show a future closing date on a market closed early', async () => {
    // An admin can close a market before its close_time, and close_time stays
    // in the future — so the row must go by the status, not only the clock.
    mockList([
      { id: 'g7', draft_key: 'k7', status: 'closed', question: 'Closed early by an admin?', close_time: '2099-01-05T12:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
    ])
    renderPage()
    await screen.findByText('Closed early by an admin?')
    expect(screen.queryByText(/closes/i)).not.toBeInTheDocument()
    // A past close_time also renders "Closed" in the status badge, so two elements match.
    expect(screen.getAllByText('Closed').length).toBeGreaterThanOrEqual(1)
  })

  it('offers Propose Outcome only for closed markets', async () => {
    mockList(mockMarkets)
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    const links = screen.getAllByRole('link', { name: /propose outcome/i })
    expect(links).toHaveLength(1)
  })

  it('navigates to the propose-outcome page for a closed market', async () => {
    mockList(mockMarkets)
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')

    await actor.click(screen.getByRole('link', { name: /propose outcome/i }))

    expect(await screen.findByText('propose outcome')).toBeInTheDocument()
  })
})
