import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import MarketExposurePage from './MarketExposurePage'

const LEDGER_BASE = 'http://localhost:8003'
const MARKET_BASE = 'http://localhost:8001'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

function market(overrides: Record<string, unknown> = {}) {
  return {
    id: 'mkt-1',
    status: 'open',
    question: 'Will it rain in Singapore tomorrow?',
    description: null,
    outcomes: [
      { id: 'o-yes', position: 0, label: 'Yes' },
      { id: 'o-no', position: 1, label: 'No' },
    ],
    close_time: '2099-01-05T12:00:00Z',
    resolution_time: '2099-01-10T12:00:00Z',
    resolution_criteria: 'Resolves YES if it rains.',
    resolution_sources: [],
    liquidity_b: '100.0000',
    seed_subsidy: '250.0000',
    published_at: '2026-01-01T00:00:00Z',
    proposed_outcome_id: null,
    ...overrides,
  }
}

function mockMarket(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${MARKET_BASE}/public/markets/${id}`, () => HttpResponse.json(body)))
}

function mockExposure(id: string, body: Record<string, unknown>) {
  server.use(http.get(`${LEDGER_BASE}/ledger/markets/${id}/exposure`, () => HttpResponse.json({ market_id: id, ...body })))
}

function renderPage(marketId = 'mkt-1') {
  return render(
    <WithProviders user={admin}>
      <MemoryRouter initialEntries={[`/admin/markets/${marketId}/exposure`]}>
        <Routes>
          <Route path="/admin/markets/:id/exposure" element={<MarketExposurePage />} />
          <Route path="/admin/markets" element={<p>all markets</p>} />
        </Routes>
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('MarketExposurePage', () => {
  it('shows the pool and the per-outcome exposure table', async () => {
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      pool: '1250.0000',
      payout_exceeds_pool: false,
      result: null,
      winning_outcome_id: null,
      outcomes: [
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', payout_if_wins: '120.0000', residue_if_wins: '1130.0000' },
        { outcome_id: 'o-no', position: 1, shares_outstanding: '45.0000', payout_if_wins: '45.0000', residue_if_wins: '1205.0000' },
      ],
    })

    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(await screen.findByText('1,250.0000')).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByText(/exceeds the pool/i)).not.toBeInTheDocument()
  })

  it('states the worst case: the outcome the pool covers least well, and what it leaves', async () => {
    // Nothing is short here, and the admin still gets the figure: the
    // smallest residue of the two, named with the outcome that produces it.
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      pool: '1250.0000',
      payout_exceeds_pool: false,
      result: null,
      winning_outcome_id: null,
      outcomes: [
        { outcome_id: 'o-no', position: 1, shares_outstanding: '45.0000', payout_if_wins: '45.0000', residue_if_wins: '1205.0000' },
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '120.0000', payout_if_wins: '120.0000', residue_if_wins: '1130.0000' },
      ],
    })

    renderPage()

    expect(await screen.findByText('If Yes wins, the pool keeps 1,130.0000.')).toBeInTheDocument()
  })

  it('states the worst case as a top-up, and names the offending outcome in the alert', async () => {
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      pool: '100.0000',
      payout_exceeds_pool: true,
      result: null,
      winning_outcome_id: null,
      outcomes: [
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '500.0000', payout_if_wins: '500.0000', residue_if_wins: '-400.0000' },
        { outcome_id: 'o-no', position: 1, shares_outstanding: '20.0000', payout_if_wins: '20.0000', residue_if_wins: '80.0000' },
      ],
    })

    renderPage()

    expect(await screen.findByText('If Yes wins, the platform tops up 400.0000.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('The payout for Yes exceeds the pool.')
  })

  it('shows a 200-with-nothing market as no trades yet, not an error', async () => {
    // docs/api/ledger-service.md: a market the ledger has never opened a book
    // for reads as 200 with no outcomes, null figures and the flag down.
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', { pool: null, payout_exceeds_pool: false, result: null, winning_outcome_id: null, outcomes: [] })

    renderPage()

    expect(await screen.findByText(/no trades yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows a market whose book is open but untraded as no trades yet, like one with no book', async () => {
    // #517. A book opens on the market's first preview or snapshot, before
    // anyone trades (ledger-service.md). From then on its exposure reads
    // state_version 0 with every outcome at zero, and nobody has traded in it
    // any more than before. Naming a worst case here would pick an outcome
    // nobody holds.
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      opened_at: '2026-01-01T00:00:00Z',
      state_version: 0,
      seed_subsidy: '250.0000',
      pool: '250.0000',
      payout_exceeds_pool: false,
      result: null,
      winning_outcome_id: null,
      outcomes: [
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '0.0000', payout_if_wins: '0.0000', residue_if_wins: '250.0000' },
        { outcome_id: 'o-no', position: 1, shares_outstanding: '0.0000', payout_if_wins: '0.0000', residue_if_wins: '250.0000' },
      ],
    })

    renderPage()

    expect(await screen.findByText(/no trades yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText(/if yes wins/i)).not.toBeInTheDocument()
  })

  it('passes over a residue it cannot read, rather than naming it the worst case', async () => {
    // A figure that does not parse shows as "—", so calling it the worst
    // case would hide a real shortfall behind a dash.
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      pool: '100.0000',
      payout_exceeds_pool: true,
      result: null,
      winning_outcome_id: null,
      outcomes: [
        { outcome_id: 'o-no', position: 1, shares_outstanding: '20.0000', payout_if_wins: '20.0000', residue_if_wins: '1E-4' },
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '500.0000', payout_if_wins: '500.0000', residue_if_wins: '-400.0000' },
      ],
    })

    renderPage()

    expect(await screen.findByText('If Yes wins, the platform tops up 400.0000.')).toBeInTheDocument()
  })

  it('names the winning outcome on a settled market, instead of a pool to protect', async () => {
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', {
      pool: null,
      payout_exceeds_pool: false,
      result: 'settled',
      winning_outcome_id: 'o-yes',
      outcomes: [
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '500.0000', payout_if_wins: null, residue_if_wins: null },
      ],
    })

    renderPage()

    expect(await screen.findByText('This market has settled; Yes won and the pool has been paid out.')).toBeInTheDocument()
    expect(screen.queryByText('Pool')).not.toBeInTheDocument()
  })

  it('says a voided market has no winner and an emptied pool, instead of drawing a live pool', async () => {
    // #526: a void empties the pool as settlement does, so pool and every
    // payout are null and the flag is false (ledger-service.md).
    mockMarket('mkt-1', market({ status: 'voided' }))
    mockExposure('mkt-1', {
      pool: null,
      payout_exceeds_pool: false,
      result: 'voided',
      winning_outcome_id: null,
      outcomes: [
        { outcome_id: 'o-yes', position: 0, shares_outstanding: '500.0000', payout_if_wins: null, residue_if_wins: null },
      ],
    })

    renderPage()

    expect(await screen.findByText('This market was voided, so it has no winner, and its pool has been emptied. What it refunded is in the audit entry for the void.')).toBeInTheDocument()
    expect(screen.queryByText('Pool')).not.toBeInTheDocument()
    expect(screen.queryByText(/won/)).not.toBeInTheDocument()
    expect(screen.queryByText(/settled/i)).not.toBeInTheDocument()
  })

  it('says when the figures were read, which trade they reflect, and reads them again on Refresh', async () => {
    // Exposure moves with every trade, so an admin who left the tab open
    // through a burst must be able to see that and ask again (#361).
    mockMarket('mkt-1', market())
    let reads = 0
    server.use(http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/exposure`, () => {
      reads += 1
      return HttpResponse.json({
        market_id: 'mkt-1',
        state_version: reads,
        pool: reads === 1 ? '100.0000' : '250.0000',
        payout_exceeds_pool: false,
        result: null,
        winning_outcome_id: null,
        outcomes: [{ outcome_id: 'o-yes', position: 0, shares_outstanding: '10.0000', payout_if_wins: '10.0000', residue_if_wins: reads === 1 ? '90.0000' : '240.0000' }],
      })
    }))
    const actor = userEvent.setup()
    renderPage()
    expect(await screen.findByText('100.0000')).toBeInTheDocument()
    expect(screen.getByText(/read at .* · state version 1$/i)).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: 'Refresh' }))

    expect(await screen.findByText('250.0000')).toBeInTheDocument()
    expect(screen.getByText(/read at .* · state version 2$/i)).toBeInTheDocument()
  })

  it('shows an error when the market fails to load', async () => {
    server.use(http.get(`${MARKET_BASE}/public/markets/mkt-1`, () => HttpResponse.error()))
    mockExposure('mkt-1', { pool: null, payout_exceeds_pool: false, result: null, winning_outcome_id: null, outcomes: [] })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load this market/i)
  })

  it('shows an error when exposure fails to load, independent of the market', async () => {
    mockMarket('mkt-1', market())
    server.use(http.get(`${LEDGER_BASE}/ledger/markets/mkt-1/exposure`, () => HttpResponse.error()))

    renderPage()

    expect(await screen.findByText('Will it rain in Singapore tomorrow?')).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load exposure/i)
  })

  it('shows a link back to all markets', async () => {
    mockMarket('mkt-1', market())
    mockExposure('mkt-1', { pool: null, payout_exceeds_pool: false, result: null, winning_outcome_id: null, outcomes: [] })
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will it rain in Singapore tomorrow?')

    await actor.click(screen.getByRole('link', { name: '← All Markets' }))

    expect(screen.getByText('all markets')).toBeInTheDocument()
  })
})
