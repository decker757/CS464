import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../../context/AuthContext'
import type { User } from '../../context/AuthContext'
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

function renderPage() {
  return render(
    <AuthContext.Provider value={{ user: admin, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={['/admin/markets/new']}>
        <Routes>
          <Route path="/admin/markets/new" element={<CreateMarketPage />} />
          <Route path="/markets" element={<p>markets list</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
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

  it('autosave fires after 3 seconds and shows Saved', async () => {
    vi.useFakeTimers()
    renderPage()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByText('Saved')).toBeInTheDocument()
  })

  it('shows initial_price beside outcomes after first autosave', async () => {
    vi.useFakeTimers()
    renderPage()
    await act(() => vi.advanceTimersByTimeAsync(3100))
    const prices = screen.getAllByText('50.0% start')
    expect(prices.length).toBe(2)
  })

  it('shows max_platform_loss from server response', async () => {
    vi.useFakeTimers()
    renderPage()
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
    await act(() => vi.advanceTimersByTimeAsync(3100))
    expect(screen.getByText('A close time is required.')).toBeInTheDocument()
    expect(screen.getByText('A seed subsidy is required.')).toBeInTheDocument()
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
})
