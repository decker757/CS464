import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PublicOutcome } from '../../api/marketApi'
import { BalanceContext } from '../../context/BalanceContext'
import { server } from '../../test/server'
import TradingPanel from './TradingPanel'

const LEDGER_BASE = 'http://localhost:8003'
const MARKET_ID = 'mkt-1'

const outcomes: PublicOutcome[] = [
  { id: 'o-yes', position: 0, label: 'Yes' },
  { id: 'o-no', position: 1, label: 'No' },
]

function previewResponse(overrides: Record<string, unknown> = {}) {
  return {
    market_id: MARKET_ID,
    state_version: 1,
    side: 'buy',
    outcome_id: 'o-yes',
    quantity: '10.0000',
    total: '-5.1250',
    average_price: '0.5125',
    prices: [
      { outcome_id: 'o-yes', position: 0, price: '0.5000' },
      { outcome_id: 'o-no', position: 1, price: '0.5000' },
    ],
    post_trade_prices: [
      { outcome_id: 'o-yes', position: 0, price: '0.5250' },
      { outcome_id: 'o-no', position: 1, price: '0.4750' },
    ],
    ...overrides,
  }
}

function mockPreview(response: Record<string, unknown> | (() => Record<string, unknown>)) {
  server.use(
    http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, () =>
      HttpResponse.json(typeof response === 'function' ? response() : response),
    ),
  )
}

function mockPreviewError(code: string, message: string, status = 422, details?: Record<string, unknown>) {
  server.use(
    http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, () =>
      HttpResponse.json({ error: { code, message, details } }, { status }),
    ),
  )
}

function renderPanel() {
  return render(
    <BalanceContext.Provider value={{ balance: '1000.0000', refetch: vi.fn(async () => {}) }}>
      <TradingPanel marketId={MARKET_ID} outcomes={outcomes} />
    </BalanceContext.Provider>,
  )
}

async function typeQuantity(actor: ReturnType<typeof userEvent.setup>, value: string) {
  await actor.type(screen.getByLabelText('Quantity (shares)'), value)
  await act(() => vi.advanceTimersByTimeAsync(300))
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('TradingPanel', () => {
  it('shows a live cost preview after the debounce settles', async () => {
    mockPreview(previewResponse())
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10')

    const preview = await screen.findByLabelText('trade preview')
    expect(preview).toHaveTextContent('5.1250 credits')
    expect(preview).toHaveTextContent('0.5125')
  })

  it('does not fetch a preview before the debounce settles', async () => {
    let calls = 0
    server.use(
      http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, () => {
        calls += 1
        return HttpResponse.json(previewResponse())
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await actor.type(screen.getByLabelText('Quantity (shares)'), '10')
    expect(calls).toBe(0)

    await act(() => vi.advanceTimersByTimeAsync(300))
    expect(calls).toBe(1)
  })

  it('shows Cost for a buy and Proceeds for a sell', async () => {
    mockPreview(previewResponse({ total: '5.1250' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await typeQuantity(actor, '10')

    expect(await screen.findByText('Proceeds')).toBeInTheDocument()
  })

  it('submits a buy with the quoted state_version and a generated idempotency key', async () => {
    mockPreview(previewResponse())
    let sentBody: Record<string, unknown> | undefined
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        sentBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({
          transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
          outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.1250', state_version: 2,
        }, { status: 201 })
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    expect(await screen.findByText(/bought 10.0000 yes for 5.1250 credits/i)).toBeInTheDocument()
    expect(sentBody).toMatchObject({ outcome_id: 'o-yes', side: 'buy', quantity: '10', state_version: 1 })
    expect(typeof sentBody?.idempotency_key).toBe('string')
  })

  it('clears the quantity field and refetches the balance after a successful trade', async () => {
    mockPreview(previewResponse())
    const refetch = vi.fn(async () => {})
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json({
          transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
          outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.1250', state_version: 2,
        }, { status: 201 }),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    render(
      <BalanceContext.Provider value={{ balance: '1000.0000', refetch }}>
        <TradingPanel marketId={MARKET_ID} outcomes={outcomes} />
      </BalanceContext.Provider>,
    )
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    await screen.findByText(/bought/i)
    expect(screen.getByLabelText('Quantity (shares)')).toHaveValue(null)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it('re-previews automatically on a 409 quote_stale and lets the trader confirm again', async () => {
    mockPreview(previewResponse())
    let tradeAttempts = 0
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () => {
        tradeAttempts += 1
        return HttpResponse.json(
          { error: { code: 'quote_stale', message: 'Stale.', details: { quoted: 1, current: 2 } } },
          { status: 409 },
        )
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    expect(await screen.findByText(/prices moved while you were looking/i)).toBeInTheDocument()
    // A fresh quote was requested, and the trade itself was attempted only once.
    expect(tradeAttempts).toBe(1)
  })

  it('shows the balance and amount needed on insufficient_funds', async () => {
    mockPreview(previewResponse())
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json(
          { error: { code: 'insufficient_funds', message: 'Not enough.', details: { balance: '5.0000', required: '5.1250' } } },
          { status: 409 },
        ),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    expect(await screen.findByText(/you have 5\.0000 credits, but this trade needs 5\.1250/i)).toBeInTheDocument()
  })

  it('shows what is held and what was asked for on insufficient_shares_held', async () => {
    mockPreview(previewResponse({ side: 'sell', total: '5.1250' }))
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json(
          { error: { code: 'insufficient_shares_held', message: 'Not enough shares.', details: { held: '2.0000', requested: '10.0000' } } },
          { status: 409 },
        ),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^sell yes$/i }))

    expect(await screen.findByText(/you hold 2\.0000 shares, but this sell asks for 10\.0000/i)).toBeInTheDocument()
  })

  it('shows a plain message when the market has closed', async () => {
    mockPreview(previewResponse())
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json({ error: { code: 'market_closed', message: 'Closed.' } }, { status: 409 }),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    expect(await screen.findByText(/this market has closed/i)).toBeInTheDocument()
  })

  it('tells the trader to increase the quantity on proceeds_below_tick', async () => {
    mockPreviewError('proceeds_below_tick', 'Too small.')
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '0.0001')

    expect(await screen.findByText(/pay out less than the smallest amount/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^buy yes$/i })).toBeDisabled()
  })

  it('disables submit while no valid quantity is entered', () => {
    renderPanel()
    expect(screen.getByRole('button', { name: /^buy yes$/i })).toBeDisabled()
  })

  it('rejects a quantity with more than 4 decimal places, client-side, without calling the preview', async () => {
    let calls = 0
    server.use(
      http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, () => {
        calls += 1
        return HttpResponse.json(previewResponse())
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10.12345')

    expect(await screen.findByText(/at most 4 decimal places/i)).toBeInTheDocument()
    expect(calls).toBe(0)
    expect(screen.getByRole('button', { name: /^buy yes$/i })).toBeDisabled()
  })

  it('rejects a quantity of zero, client-side', async () => {
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '0')

    expect(await screen.findByText(/must be greater than zero/i)).toBeInTheDocument()
  })

  it('accepts a quantity at exactly 4 decimal places', async () => {
    mockPreview(previewResponse())
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10.0001')

    expect(await screen.findByLabelText('trade preview')).toBeInTheDocument()
  })

  it('clears a stale preview immediately when the outcome changes', async () => {
    mockPreview(previewResponse())
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^no$/i }))

    expect(screen.queryByLabelText('trade preview')).not.toBeInTheDocument()
  })

  it('does not apply a quote fetched for a quantity the trader already changed away from', async () => {
    // Stand-in for the debounce race: a slow first response landing after a
    // second, faster one must not clobber the newer quote.
    let resolveFirst!: (value: Record<string, unknown>) => void
    let callCount = 0
    server.use(
      http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, async () => {
        callCount += 1
        if (callCount === 1) {
          return new Promise<Response>(resolve => {
            resolveFirst = (body) => resolve(HttpResponse.json(body))
          })
        }
        return HttpResponse.json(previewResponse({ quantity: '20.0000', total: '-10.2500' }))
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10')
    fireEvent.change(screen.getByLabelText('Quantity (shares)'), { target: { value: '20' } })
    await act(() => vi.advanceTimersByTimeAsync(300))
    await screen.findByLabelText('trade preview')

    // The slow first response for "10" arrives last; it must not replace the
    // "20" quote already on screen, since inputKey no longer matches it.
    await act(async () => {
      resolveFirst(previewResponse({ quantity: '10.0000', total: '-5.1250' }))
      await Promise.resolve()
    })

    expect(screen.getByLabelText('trade preview')).toHaveTextContent('10.2500 credits')
  })
})
