import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { MarketOverviewRow } from '../../api/marketApi'
import { AuthContext } from '../../context/AuthContext'
import type { User } from '../../context/AuthContext'
import { server } from '../../test/server'
import AdminMarketsPage from './AdminMarketsPage'

const MARKET_BASE = 'http://localhost:8001'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

const OTHER_ADMIN_ID = 'u2'

// The admin's own markets, one in each status, in the order the server sends
// them: soonest close first, no close time last.
const mockMarkets: MarketOverviewRow[] = [
  { id: 'd4', creator_id: admin.id, status: 'closed', question: 'Will inflation fall below 2%?', close_time: '2025-01-01T00:00:00Z' },
  { id: 'f6', creator_id: admin.id, status: 'approved', question: 'Did it rain yesterday?', close_time: '2025-05-01T00:00:00Z' },
  { id: 'e5', creator_id: admin.id, status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-06-01T00:00:00Z' },
  { id: 'c3', creator_id: admin.id, status: 'open', question: 'Will SMU win SUNIG?', close_time: '2099-01-05T12:00:00Z' },
  { id: 'b2', creator_id: admin.id, status: 'submitted', question: 'Will X happen?', close_time: '2099-03-01T00:00:00Z' },
  { id: 'a1', creator_id: admin.id, status: 'draft', question: 'Untitled draft', close_time: null },
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

// GET /markets/overview, with the counts the server would send for these rows:
// every status present, zero when none.
function mockList(markets: MarketOverviewRow[]) {
  const counts = { draft: 0, submitted: 0, open: 0, closed: 0, pending_resolution: 0, approved: 0 }
  for (const market of markets) counts[market.status] += 1
  server.use(http.get(`${MARKET_BASE}/markets/overview`, () => HttpResponse.json({ markets, counts })))
}

describe('AdminMarketsPage', () => {
  it('shows a row for each of the caller\'s markets', async () => {
    mockList(mockMarkets)
    renderPage()
    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
    expect(screen.getByText('Untitled draft')).toBeInTheDocument()
  })

  it('marks only the markets the signed-in admin created as theirs', async () => {
    mockList([
      ...mockMarkets,
      { id: 'x9', creator_id: OTHER_ADMIN_ID, status: 'pending_resolution', question: 'Another admin\'s market?', close_time: '2025-07-01T00:00:00Z' },
    ])
    renderPage()
    const theirs = await screen.findByText('Another admin\'s market?')
    expect(theirs.closest('div')).not.toHaveTextContent(/created by you/i)
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

  it('keeps the server\'s order: soonest close time first, no close time last', async () => {
    mockList(mockMarkets)
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const questions = screen.getAllByText(/^(Untitled draft|Will |Did )/).map(el => el.textContent)
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
    server.use(http.get(`${MARKET_BASE}/markets/overview`, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('does not show a future closing date on a market closed early', async () => {
    // An admin can close a market before its close_time, and close_time stays
    // in the future — so the row must go by the status, not only the clock.
    mockList([
      { id: 'g7', creator_id: admin.id, status: 'closed', question: 'Closed early by an admin?', close_time: '2099-01-05T12:00:00Z' },
    ])
    renderPage()
    await screen.findByText('Closed early by an admin?')
    expect(screen.queryByText(/closes/i)).not.toBeInTheDocument()
    // A past close_time also renders "Closed" in the status badge, so two elements match.
    expect(screen.getAllByText('Closed').length).toBeGreaterThanOrEqual(1)
  })

  it('offers Propose Outcome only for closed markets the admin created', async () => {
    // Only the creator may propose; for anyone else the request is a 404.
    mockList([
      ...mockMarkets,
      { id: 'y8', creator_id: OTHER_ADMIN_ID, status: 'closed', question: 'Another admin\'s closed market?', close_time: '2025-02-01T00:00:00Z' },
    ])
    renderPage()
    await screen.findByText('Another admin\'s closed market?')

    const links = screen.getAllByRole('link', { name: /propose outcome/i })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', '/admin/markets/d4/propose-outcome')
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
