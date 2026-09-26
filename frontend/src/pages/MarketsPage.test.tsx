import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { server } from '../test/server'
import MarketsPage from './MarketsPage'

const MARKET_BASE = 'http://localhost:8001'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

const mockMarkets = [
  { id: 'a1', status: 'open',   question: 'Will SMU win SUNIG?',          close_time: '2099-01-05T12:00:00Z' },
  { id: 'b2', status: 'closed', question: 'Will inflation fall below 2%?', close_time: '2025-01-01T00:00:00Z' },
]

function renderPage() {
  return render(
    <AuthContext.Provider value={{ user: trader, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={['/markets']}>
        <Routes>
          <Route path="/markets" element={<MarketsPage />} />
          <Route path="/markets/:id" element={<p>market detail</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('MarketsPage', () => {
  it('shows a card for each market returned by the API', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: mockMarkets }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
  })

  it('shows status badges for each market', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: mockMarkets }),
      ),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    expect(screen.getByText('Open')).toBeInTheDocument()
    // A past close_time also renders "Closed" in the timestamp slot, so two elements match.
    expect(screen.getAllByText('Closed').length).toBeGreaterThanOrEqual(1)
  })

  it('shows an empty state when no markets are returned', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: [] }),
      ),
    )
    renderPage()
    expect(await screen.findByText(/no markets available/i)).toBeInTheDocument()
  })

  it('shows an error when the API fails', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.error()),
    )
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('navigates to the market detail page when a card is clicked', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: [mockMarkets[0]] }),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await actor.click(await screen.findByText('Will SMU win SUNIG?'))
    expect(screen.getByText('market detail')).toBeInTheDocument()
  })
})
