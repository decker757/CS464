import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { server } from '../test/server'
import MarketsPage from './MarketsPage'

const MARKET_BASE = 'http://localhost:8001'
const SNAPSHOT = 'http://localhost:8003/ledger/markets/:id/snapshot'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

const mockMarkets = [
  { id: 'a1', status: 'open',   question: 'Will SMU win SUNIG?',          close_time: '2099-01-05T12:00:00Z' },
  { id: 'b2', status: 'closed', question: 'Will inflation fall below 2%?', close_time: '2025-01-01T00:00:00Z' },
]

function snapshotFor(marketId: string, yes: string, no: string) {
  return {
    market_id: marketId,
    state_version: 3,
    prices: [
      { outcome_id: `${marketId}-yes`, position: 0, price: yes },
      { outcome_id: `${marketId}-no`, position: 1, price: no },
    ],
    occurred_at: '2026-09-15T09:12:44.318000+00:00',
  }
}

// The link that wraps the card holding this question.
function cardFor(question: string): HTMLElement {
  const card = screen.getByText(question).closest('a')
  if (!card) throw new Error(`no card for ${question}`)
  return card
}

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
  // Every card asks the ledger for its prices; tests about other things get a
  // plain answer rather than an unhandled request.
  beforeEach(() => {
    server.use(
      http.get(SNAPSHOT, ({ params }) => HttpResponse.json(snapshotFor(String(params.id), '0.5000', '0.5000'))),
    )
  })

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

  it('does not show a future closing date on a market closed early', async () => {
    // An admin can close a market before its close_time, and close_time stays
    // in the future — so the card must go by the status, not only the clock.
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({
          markets: [{ id: 'c3', status: 'closed', question: 'Closed early by an admin?', close_time: '2099-01-05T12:00:00Z' }],
        }),
      ),
    )
    renderPage()
    await screen.findByText('Closed early by an admin?')
    expect(screen.queryByText(/closes/i)).not.toBeInTheDocument()
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

  // [X-1] #126 AC 1 and AC 3. 0.1235 is a price a float would show as 12.3%.
  it("shows each card's YES/NO prices from that market's snapshot", async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: mockMarkets })),
      http.get(SNAPSHOT, ({ params }) =>
        params.id === 'a1'
          ? HttpResponse.json(snapshotFor('a1', '0.1235', '0.8765'))
          : HttpResponse.json(snapshotFor('b2', '0.7000', '0.3000')),
      ),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    const open = cardFor('Will SMU win SUNIG?')
    expect(await within(open).findByLabelText('Yes price')).toHaveTextContent('12.4%')
    expect(within(open).getByLabelText('No price')).toHaveTextContent('87.7%')

    // A closed market still has a last price, and shows it.
    const closed = cardFor('Will inflation fall below 2%?')
    expect(await within(closed).findByLabelText('Yes price')).toHaveTextContent('70.0%')
    expect(within(closed).getByLabelText('No price')).toHaveTextContent('30.0%')
  })

  // [X-1] #126 AC 2: one market's price failing does not take its card, or
  // anyone else's price, with it.
  it('shows a dash for a price that cannot be loaded, and still shows the market', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: mockMarkets })),
      http.get(SNAPSHOT, ({ params }) =>
        params.id === 'a1'
          ? HttpResponse.json(
              { error: { code: 'market_terms_unavailable', message: 'Try again.' } },
              { status: 503 },
            )
          : HttpResponse.json(snapshotFor('b2', '0.7000', '0.3000')),
      ),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    const closed = cardFor('Will inflation fall below 2%?')
    expect(await within(closed).findByLabelText('Yes price')).toHaveTextContent('70.0%')

    const failed = cardFor('Will SMU win SUNIG?')
    expect(within(failed).getByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(within(failed).getByLabelText('Yes price')).toHaveTextContent('—')
    expect(within(failed).getByLabelText('No price')).toHaveTextContent('—')
  })
})
