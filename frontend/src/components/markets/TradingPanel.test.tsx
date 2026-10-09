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
    expect(preview).toHaveTextContent('51.3%')
  })

  it('shows how the trade moves the selected outcome\'s price', async () => {
    mockPreview(previewResponse({ outcome_id: 'o-no' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    // Yes is both the default and first in the outcomes list, so selecting
    // No catches a preview that reads prices[0] instead of matching by id.
    await actor.click(screen.getByRole('button', { name: 'No' }))
    await typeQuantity(actor, '10')

    const preview = await screen.findByLabelText('trade preview')
    expect(preview).toHaveTextContent('50.0% → 47.5%')
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
    mockPreview(previewResponse())
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10')
    expect(await screen.findByText('Cost')).toBeInTheDocument()

    mockPreview(previewResponse({ total: '5.1250' }))
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await actor.clear(screen.getByLabelText('Quantity (shares)'))
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

  it('re-previews automatically on a 409 quote_stale, re-enables submit, and sends the same key with the new state_version on confirm', async () => {
    let previewCalls = 0
    server.use(
      http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, () => {
        previewCalls += 1
        return HttpResponse.json(previewResponse({ state_version: previewCalls === 1 ? 1 : 2 }))
      }),
    )
    let tradeAttempts = 0
    let firstKey: unknown
    let secondBody: Record<string, unknown> | undefined
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        tradeAttempts += 1
        const body = await request.json() as Record<string, unknown>
        if (tradeAttempts === 1) {
          firstKey = body.idempotency_key
          return HttpResponse.json(
            { error: { code: 'quote_stale', message: 'Stale.', details: { quoted: 1, current: 2 } } },
            { status: 409 },
          )
        }
        secondBody = body
        return HttpResponse.json({
          transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
          outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.1250', state_version: 3,
        }, { status: 201 })
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    // The re-quote actually ran and the message moved on from "Getting a
    // fresh quote…" to say the quote is ready, rather than staying frozen
    // on the original message regardless of how the re-quote turned out.
    expect(await screen.findByText(/check the new quote and confirm again/i)).toBeInTheDocument()
    expect(screen.queryByText(/getting a fresh quote/i)).not.toBeInTheDocument()
    // The re-quote actually ran: a second preview call landed, and the
    // button is enabled again rather than stuck disabled or mid-submit.
    expect(previewCalls).toBe(2)
    const submitButton = screen.getByRole('button', { name: /^buy yes$/i })
    expect(submitButton).toBeEnabled()

    await actor.click(submitButton)

    expect(await screen.findByText(/bought/i)).toBeInTheDocument()
    expect(tradeAttempts).toBe(2)
    expect(secondBody).toMatchObject({ state_version: 2, idempotency_key: firstKey })
  })

  it('clears "Getting a fresh quote…" rather than leaving it on screen when the re-quote itself fails', async () => {
    let previewCalls = 0
    server.use(
      http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, () => {
        previewCalls += 1
        if (previewCalls === 1) return HttpResponse.json(previewResponse({ state_version: 1 }))
        return HttpResponse.json({ error: { code: 'market_closed', message: 'Closed.' } }, { status: 409 })
      }),
    )
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json(
          { error: { code: 'quote_stale', message: 'Stale.', details: { quoted: 1, current: 2 } } },
          { status: 409 },
        ),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    // previewError already explains the failed re-quote; submitError must
    // not also show the now-stale "Getting a fresh quote…".
    await screen.findByText(/this market has closed/i)
    expect(screen.queryByText(/getting a fresh quote/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/check the new quote and confirm again/i)).not.toBeInTheDocument()
  })

  it('shows the balance and amount needed on insufficient_funds, formatted like everywhere else', async () => {
    mockPreview(previewResponse())
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json(
          { error: { code: 'insufficient_funds', message: 'Not enough.', details: { balance: '1234.5678', required: '5.1250' } } },
          { status: 409 },
        ),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    expect(await screen.findByText(/you have 1,234\.5678 credits, but this trade needs 5\.1250/i)).toBeInTheDocument()
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

  it('gives insufficient_shares_outstanding its own message instead of the generic one', async () => {
    // Only the preview can return this (ledger-service.md); the trade route
    // never does, because insufficient_shares_held always refuses first.
    mockPreviewError('insufficient_shares_outstanding', 'No shares outstanding yet.', 409)
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))

    await typeQuantity(actor, '10')

    expect(await screen.findByText(/nobody holds enough shares of this outcome yet/i)).toBeInTheDocument()
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument()
  })

  it('keeps the same idempotency key across a retry after a network error', async () => {
    // A dropped connection could mean the trade went through and only the
    // reply was lost. Retrying with a new key would let the server treat
    // it as a second, different trade and charge twice.
    mockPreview(previewResponse())
    let attempts = 0
    const keys: unknown[] = []
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        attempts += 1
        const body = await request.json() as Record<string, unknown>
        keys.push(body.idempotency_key)
        if (attempts === 1) return HttpResponse.error()
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
    expect(await screen.findByText(/something went wrong/i)).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    expect(await screen.findByText(/bought/i)).toBeInTheDocument()

    expect(attempts).toBe(2)
    expect(keys[0]).toBe(keys[1])
  })

  it('keeps the same idempotency key across a retry after a 500', async () => {
    mockPreview(previewResponse())
    let attempts = 0
    const keys: unknown[] = []
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        attempts += 1
        const body = await request.json() as Record<string, unknown>
        keys.push(body.idempotency_key)
        if (attempts === 1) {
          return HttpResponse.json({ error: { code: 'market_book_incomplete', message: 'Server fault.' } }, { status: 500 })
        }
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
    await screen.findByRole('alert')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    expect(await screen.findByText(/bought/i)).toBeInTheDocument()

    expect(attempts).toBe(2)
    expect(keys[0]).toBe(keys[1])
  })

  it('generates a new idempotency key after a definite 4xx rejection', async () => {
    mockPreview(previewResponse())
    const keys: unknown[] = []
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>
        keys.push(body.idempotency_key)
        return HttpResponse.json(
          { error: { code: 'insufficient_funds', message: 'Not enough.', details: { balance: '1.0000', required: '5.1250' } } },
          { status: 409 },
        )
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/you have 1\.0000 credits/i)
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/you have 1\.0000 credits/i)

    expect(keys).toHaveLength(2)
    expect(keys[0]).not.toBe(keys[1])
  })

  it('clears the submit error when the trader changes the quantity', async () => {
    mockPreview(previewResponse())
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json(
          { error: { code: 'insufficient_funds', message: 'Not enough.', details: { balance: '1.0000', required: '5.1250' } } },
          { status: 409 },
        ),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/you have 1\.0000 credits/i)

    mockPreview(previewResponse({ total: '-10.2500' }))
    await typeQuantity(actor, '1')

    expect(screen.queryByText(/you have 1\.0000 credits/i)).not.toBeInTheDocument()
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

  it('tells the trader the quantity is not valid on invalid_request', async () => {
    mockPreviewError('invalid_request', 'Invalid.', 422)
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10')

    expect(await screen.findByText(/that quantity isn't valid/i)).toBeInTheDocument()
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
