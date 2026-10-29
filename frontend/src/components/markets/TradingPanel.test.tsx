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

// More shares of either outcome than any test here sells, so the sell clamp
// is never what refuses a trade unless a test says what is held.
const HELD_PLENTY = { 'o-yes': '100.0000', 'o-no': '100.0000' }

interface PanelOptions {
  balance?: string | null | undefined
  // What a trade may spend: `balance` less what resting buy orders reserve.
  // Defaults to `balance`, an account with no resting orders.
  available?: string | null | undefined
  heldByOutcome?: Record<string, string> | null
  reservedSharesByOutcome?: Record<string, string>
  // The outcome chosen on the page's price blocks (#429). Yes by default, the
  // page's own default.
  outcomeId?: string
  refetch?: () => Promise<void>
  onTraded?: () => void
  // Off by default so these tests drive the market-order body directly, as
  // they did before the order-type toggle existed. The limit half has its
  // own file.
  limitOrdersAllowed?: boolean
  onOrderPlaced?: () => void
}

function panel({
  balance = '1000.0000', available = balance, heldByOutcome = HELD_PLENTY, reservedSharesByOutcome = {}, outcomeId = 'o-yes',
  refetch = vi.fn(async () => {}), onTraded = vi.fn(), limitOrdersAllowed = false, onOrderPlaced = vi.fn(),
}: PanelOptions = {}) {
  return (
    <BalanceContext.Provider value={{ balance, available, refetch }}>
      <TradingPanel
        marketId={MARKET_ID}
        outcomes={outcomes}
        outcomeId={outcomeId}
        limitOrdersAllowed={limitOrdersAllowed}
        heldByOutcome={heldByOutcome}
        reservedSharesByOutcome={reservedSharesByOutcome}
        onTraded={onTraded}
        onOrderPlaced={onOrderPlaced}
      />
    </BalanceContext.Provider>
  )
}

function renderPanel(options: PanelOptions = {}) {
  return render(panel(options))
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
    // Average price is credits per share, not a probability (#247): shown at
    // the same precision as every other credits figure, never as a percentage,
    // and carrying the unit, because a bare decimal sitting between a credits
    // row and a percentage row reads as a probability (#430).
    expect(preview).toHaveTextContent('0.5125 credits/share')
    expect(preview).not.toHaveTextContent('51.3%')
  })

  it('shows how the trade moves the selected outcome\'s price', async () => {
    mockPreview(previewResponse({ outcome_id: 'o-no' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    // Yes is both the default and first in the outcomes list, so trading No
    // catches a preview that reads prices[0] instead of matching by id.
    renderPanel({ outcomeId: 'o-no' })

    await typeQuantity(actor, '10')

    const preview = await screen.findByLabelText('trade preview')
    expect(preview).toHaveTextContent('50.0% → 47.5%')
  })

  it('shows an average price above 1.0000 on a skewed book, not clamped to a percentage', async () => {
    // ledger-service.md: average_price can pass 1.0000 on a heavily skewed
    // book. A percentage has no way to render that, which is why this field
    // is never formatted as one.
    mockPreview(previewResponse({ total: '-20.0000', average_price: '2.0000' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10')

    const preview = await screen.findByLabelText('trade preview')
    expect(preview).toHaveTextContent('2.0000')
  })

  it('heads the order type and side toggles separately, so the two rows are not one control', () => {
    // #430: the two rows are the same shape and sit a few pixels apart, and
    // the side row keeps the green/red trading convention rather than the
    // order type row's navy fill. The headings are what stops a trader
    // reading the pair as one control disagreeing with itself, and they are
    // the groups' names for a screen reader too.
    renderPanel({ limitOrdersAllowed: true })

    expect(screen.getByRole('group', { name: 'Order type' })).toContainElement(screen.getByRole('button', { name: 'Market' }))
    expect(screen.getByRole('group', { name: 'Buy or sell' })).toContainElement(screen.getByRole('button', { name: 'Buy' }))
  })

  // #429: the outcome is chosen on the page's price blocks, so the panel
  // offers no choice of its own — it says which one it is trading,
  // which the limit ticket's button otherwise never names.
  it('names the outcome it trades, and offers no outcome choice of its own', () => {
    renderPanel({ outcomeId: 'o-no', limitOrdersAllowed: true })

    expect(screen.getByText(/^Trading/)).toHaveTextContent(/^Trading No/)
    expect(screen.queryByRole('button', { name: 'Yes' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'No' })).not.toBeInTheDocument()
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

  it('clears the quantity field, refetches the balance and re-reads the holding after a successful trade', async () => {
    mockPreview(previewResponse())
    const refetch = vi.fn(async () => {})
    const onTraded = vi.fn()
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json({
          transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
          outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.1250', state_version: 2,
        }, { status: 201 }),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ refetch, onTraded })
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

    await screen.findByText(/bought/i)
    expect(screen.getByLabelText('Quantity (shares)')).toHaveValue(null)
    expect(refetch).toHaveBeenCalledTimes(1)
    // Without this the holding the sell clamp reads is the one from before
    // the buy, so selling what was just bought is refused by this panel.
    expect(onTraded).toHaveBeenCalledTimes(1)
  })

  it('confirms a buy that commits after the trader picked another outcome, naming the one it bought', async () => {
    mockPreview(previewResponse())
    let answerTrade: () => void = () => {}
    const tradeAnswered = new Promise<void>(resolve => { answerTrade = resolve })
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async () => {
        await tradeAnswered
        return HttpResponse.json({
          transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
          outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.1250', state_version: 2,
        }, { status: 201 })
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    // The No price block, clicked while the Yes buy is still in flight (#566).
    rerender(panel({ outcomeId: 'o-no' }))
    answerTrade()

    expect(await screen.findByText(/bought 10.0000 yes for 5.1250 credits/i)).toBeInTheDocument()
  })

  it('drops the confirmation once the trader types a quantity for the next trade', async () => {
    mockPreview(previewResponse())
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json({
          transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
          outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.1250', state_version: 2,
        }, { status: 201 }),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/bought 10.0000 yes/i)

    await typeQuantity(actor, '3')

    expect(screen.queryByText(/bought 10.0000 yes/i)).not.toBeInTheDocument()
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
    // The panel's own clamp reads a holding fetched before the click, so a
    // position that shrank in between still reaches this refusal — the
    // ledger is the authority on what is held, not this screen (#357).
    mockPreview(previewResponse({ side: 'sell', total: '5.1250' }))
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () =>
        HttpResponse.json(
          { error: { code: 'insufficient_shares_held', message: 'Not enough shares.', details: { held: '1234.5678', requested: '10.0000' } } },
          { status: 409 },
        ),
      ),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ heldByOutcome: { 'o-yes': '2000.0000' } })
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^sell yes$/i }))

    // Four figures and a thousands separator, so the message is pinned as
    // formatted rather than printing whatever string the ledger sent.
    expect(await screen.findByText(/you hold 1,234\.5678 shares, but this sell asks for 10\.0000/i)).toBeInTheDocument()
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
    const { rerender } = renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    // No chosen on the page's price blocks (#429), which reach the panel as
    // a new prop.
    rerender(panel({ outcomeId: 'o-no' }))

    expect(screen.queryByLabelText('trade preview')).not.toBeInTheDocument()
  })

  // #357's first finding: a share pays exactly 1 credit, so the payout is
  // the quantity and the profit is the quantity minus the cost the server
  // quoted. Without them the trader is left doing the arithmetic that turns
  // a cost into a bet.
  it('shows what a buy pays if it wins and the profit, and neither on a sell', async () => {
    mockPreview(previewResponse())
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()

    await typeQuantity(actor, '10')

    const preview = await screen.findByLabelText('trade preview')
    // 10 shares pay 10 credits and cost 5.1250, so the profit is 4.8750 —
    // exactly, which is the point of subtracting the strings.
    expect(preview).toHaveTextContent('Pays if it wins')
    expect(preview).toHaveTextContent('10.0000 credits')
    expect(preview).toHaveTextContent('+4.8750')

    // A sell gives the shares up, so there is no payout to win: showing one
    // here would promise the trader both the proceeds and the payout.
    mockPreview(previewResponse({ side: 'sell', total: '5.1250' }))
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await actor.clear(screen.getByLabelText('Quantity (shares)'))
    await typeQuantity(actor, '10')

    await screen.findByText('Proceeds')
    expect(screen.getByLabelText('trade preview')).not.toHaveTextContent('Pays if it wins')
  })

  // #357's second finding: the balance is already in context, so a trade the
  // trader cannot afford should never cost a round trip to find out.
  it('blocks a buy that costs more than the balance, before any request', async () => {
    let tradeCalls = 0
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () => {
        tradeCalls += 1
        return HttpResponse.json({ error: { code: 'insufficient_funds', message: 'Not enough.' } }, { status: 409 })
      }),
    )
    mockPreview(previewResponse({ quantity: '2000.0000', total: '-1500.0000' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ balance: '1000.0000' })

    await typeQuantity(actor, '2000')

    expect(await screen.findByText(/you have 1,000\.0000 credits, but this trade needs 1,500\.0000/i)).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: /^buy yes$/i })
    expect(submit).toBeDisabled()

    await actor.click(submit)
    expect(tradeCalls).toBe(0)
  })

  it('blocks a buy that fits the balance but not what is available, before any request', async () => {
    // ADR 0024: a resting buy order's credits stay in `balance` and cannot be
    // spent, so the check reads `available`. Here the balance would wave the
    // trade through and the ledger would then refuse it.
    mockPreview(previewResponse({ quantity: '30.0000', total: '-20.0000' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ balance: '1000.0000', available: '10.0000' })

    await typeQuantity(actor, '30')

    expect(await screen.findByRole('alert')).toHaveTextContent('You have 10.0000 credits, but this trade needs 20.0000.')
    expect(screen.getByRole('button', { name: /^buy yes$/i })).toBeDisabled()
  })

  it('allows a buy that costs exactly the balance', async () => {
    // The boundary the check must not round the wrong way: spending every
    // credit you have is a trade the ledger accepts.
    mockPreview(previewResponse({ quantity: '2000.0000', total: '-1000.0000' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ balance: '1000.0000' })

    await typeQuantity(actor, '2000')

    await screen.findByLabelText('trade preview')
    expect(screen.getByRole('button', { name: /^buy yes$/i })).toBeEnabled()
  })

  it('never blocks a sell on the balance, however little the trader has', async () => {
    // A sell pays credits in rather than taking them out, so the balance has
    // nothing to say about it: a trader with nothing left must still be able
    // to sell what they hold.
    mockPreview(previewResponse({ side: 'sell', total: '5.1250' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ balance: '0.0000' })

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await typeQuantity(actor, '10')

    await screen.findByLabelText('trade preview')
    expect(screen.getByRole('button', { name: /^sell yes$/i })).toBeEnabled()
  })

  // #357's third finding: the preview prices a sell against the shares
  // outstanding, not against the trader's own position, so it quotes
  // confidently for shares they do not hold.
  it('shows the holding on the sell side and fills the field with Max', async () => {
    mockPreview(previewResponse({ side: 'sell', total: '5.1250' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ heldByOutcome: { 'o-yes': '6.0000' } })

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    expect(screen.getByLabelText('shares held')).toHaveTextContent('You hold 6.0000 shares')

    await actor.click(screen.getByRole('button', { name: /^max$/i }))

    expect(screen.getByLabelText('Quantity (shares)')).toHaveValue(6)
  })

  it('blocks a sell of more shares than the trader holds, before any request', async () => {
    let tradeCalls = 0
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, () => {
        tradeCalls += 1
        return HttpResponse.json({ error: { code: 'insufficient_shares_held', message: 'Not enough.' } }, { status: 409 })
      }),
    )
    mockPreview(previewResponse({ side: 'sell', total: '5.1250' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ heldByOutcome: { 'o-yes': '6.0000' } })

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await typeQuantity(actor, '10')

    expect(await screen.findByText(/you hold 6\.0000 shares, but this sell asks for 10\.0000/i)).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: /^sell yes$/i })
    expect(submit).toBeDisabled()
    await actor.click(submit)
    expect(tradeCalls).toBe(0)
  })

  // #451: choosing Sell on an outcome the trader holds none of used to show a
  // quantity field, a small "You hold 0.0000 shares" and a button that could
  // never be pressed, and said why only once a quantity was typed.
  it('says on choosing Sell that the trader holds none of the outcome, and offers to buy it instead', async () => {
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ heldByOutcome: { 'o-yes': '6.0000' }, outcomeId: 'o-no' })
    // On the page before its message, which is what lets a screen reader
    // announce the message when it arrives.
    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))

    expect(screen.getByRole('status')).toHaveTextContent("You don't hold any No shares.")
    expect(screen.queryByLabelText('Quantity (shares)')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^sell no$/i })).not.toBeInTheDocument()
    // Sell stays a control the trader can reach and leave, not a disabled one.
    expect(screen.getByRole('button', { name: /^sell$/i })).toBeEnabled()

    await actor.click(screen.getByRole('button', { name: 'Buy No instead' }))

    // Still No: the offer is to buy the outcome the trader was looking at.
    expect(screen.getByRole('button', { name: 'Buy' })).toBePressed()
    expect(screen.getByRole('button', { name: /^buy no$/i })).toBeInTheDocument()
    // The offer's button has gone, so focus moves to what comes next rather
    // than dropping to the top of the page.
    expect(screen.getByLabelText('Quantity (shares)')).toHaveFocus()
  })

  it('keeps the quantity field for an outcome the trader holds, while offering the buy on the one they do not', async () => {
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel({ heldByOutcome: { 'o-yes': '6.0000' } })

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))

    expect(screen.getByLabelText('shares held')).toHaveTextContent('You hold 6.0000 shares')
    expect(screen.getByLabelText('Quantity (shares)')).toBeInTheDocument()
    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    // No chosen on the page's price blocks (#429), with Sell still pressed.
    rerender(panel({ heldByOutcome: { 'o-yes': '6.0000' }, outcomeId: 'o-no' }))

    expect(screen.getByRole('status')).toHaveTextContent("You don't hold any No shares.")
  })

  it('keeps the quantity field while the holding is loading, and offers the buy once it lands as none', async () => {
    // Null is also a read that failed, so the panel cannot take it for none:
    // that would stop a trader selling shares they own.
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel({ heldByOutcome: null })

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))

    expect(screen.getByLabelText('Quantity (shares)')).toBeInTheDocument()
    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    rerender(panel({ heldByOutcome: {} }))

    expect(screen.getByRole('status')).toHaveTextContent("You don't hold any Yes shares.")
    expect(screen.queryByLabelText('Quantity (shares)')).not.toBeInTheDocument()
  })

  // [T-6] #496: the limit ticket makes the market ticket's offer, through the
  // panel that decides it. The holding is known before Sell is pressed, which
  // is the usual case, so the region has to be on the page already on the
  // buy side for the sentence to arrive in it rather than with it.
  it('says on the limit ticket that the trader holds none of the outcome, and offers to buy it instead', async () => {
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ limitOrdersAllowed: true, heldByOutcome: { 'o-yes': '6.0000' }, outcomeId: 'o-no' })

    await actor.click(screen.getByRole('button', { name: 'Limit' }))
    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))

    // The sentence alone: the offer is a button after the region, not part
    // of what the region announces.
    expect(screen.getByRole('status')).toHaveTextContent(/^You don't hold any No shares\.$/)
    expect(screen.queryByLabelText('Limit price')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /place sell order/i })).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: 'Buy No instead' }))

    expect(screen.getByRole('button', { name: 'Buy' })).toBePressed()
    expect(screen.getByText(/^Trading/)).toHaveTextContent(/^Trading No/)
    // The limit ticket's first field, as the market ticket's offer moves on
    // to its quantity, rather than dropping focus to the top of the page.
    expect(screen.getByLabelText('Limit price')).toHaveFocus()
  })

  // #496's second criterion, asked of the panel because the panel is what
  // decides whether a holding counts as none.
  it('keeps the limit ticket for a holding its own sell orders reserve in full', async () => {
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({
      limitOrdersAllowed: true,
      heldByOutcome: { 'o-yes': '40.0000' },
      reservedSharesByOutcome: { 'o-yes': '40.0000' },
    })

    await actor.click(screen.getByRole('button', { name: 'Limit' }))
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))

    expect(screen.getByLabelText('shares held')).toHaveTextContent('You can sell 0.0000 of your 40.0000 shares')
  })

  it('blocks a sell of shares that open sell orders already reserve, before any request', async () => {
    // The ledger refuses a sell on the position less what the trader's open
    // sell orders hold back (ledger-service.md's insufficient_shares_held),
    // so the check here does too, and Max offers only what is left.
    mockPreview(previewResponse({ side: 'sell', total: '2.5000' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ heldByOutcome: { 'o-yes': '6.0000' }, reservedSharesByOutcome: { 'o-yes': '2.0000' } })

    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    expect(screen.getByLabelText('shares held')).toHaveTextContent('You can sell 4.0000 of your 6.0000 shares')

    await typeQuantity(actor, '5')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You have 4.0000 shares available to sell, but this sell asks for 5.0000. '
      + 'You hold 6.0000 in all, and 2.0000 of them are reserved by your open sell orders.',
    )
    expect(screen.getByRole('button', { name: /^sell yes$/i })).toBeDisabled()

    await actor.clear(screen.getByLabelText('Quantity (shares)'))
    await actor.click(screen.getByRole('button', { name: /^max$/i }))
    expect(screen.getByLabelText('Quantity (shares)')).toHaveValue(4)
  })

  it('offers nothing to sell, not a negative, when the reservation is above the holding', async () => {
    // Both figures come off one portfolio row (#410), so this would be the
    // ledger contradicting itself. The clamp keeps the line and the refusal
    // from going negative if it ever does.
    mockPreview(previewResponse({ side: 'sell', quantity: '0.5000', total: '0.2500' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ heldByOutcome: { 'o-yes': '1.0000' }, reservedSharesByOutcome: { 'o-yes': '2.0000' } })

    fireEvent.click(screen.getByRole('button', { name: /^sell$/i }))

    expect(screen.getByLabelText('shares held')).toHaveTextContent('You can sell 0.0000 of your 1.0000 shares')
    expect(screen.queryByRole('button', { name: /^max$/i })).not.toBeInTheDocument()
    // Shares held back in full by sell orders are still held (#451): no
    // "You don't hold any" sentence.
    expect(screen.getByRole('status')).toBeEmptyDOMElement()

    // The refusal agrees with the line above it: nothing left, never -1.0000.
    await typeQuantity(actor, '0.5')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'You have 0.0000 shares available to sell, but this sell asks for 0.5000. '
      + 'You hold 1.0000 in all, and 1.0000 of them are reserved by your open sell orders.',
    )
  })

  it('refuses nothing while the figures the two checks need are missing', async () => {
    // A balance that has not loaded (null, or still undefined) or a holding
    // read that failed must not lock the panel: the ledger refuses with the
    // exact figure either way, where clamping to what this screen does not
    // know stops a trade the trader can actually make.
    mockPreview(previewResponse({ quantity: '2000.0000', total: '-1500.0000' }))
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ balance: null, heldByOutcome: null })

    await typeQuantity(actor, '2000')

    await screen.findByLabelText('trade preview')
    expect(screen.getByRole('button', { name: /^buy yes$/i })).toBeEnabled()

    mockPreview(previewResponse({ side: 'sell', quantity: '2000.0000', total: '1500.0000' }))
    await actor.click(screen.getByRole('button', { name: /^sell$/i }))
    await act(() => vi.advanceTimersByTimeAsync(300))

    await screen.findByText('Proceeds')
    expect(screen.getByRole('button', { name: /^sell yes$/i })).toBeEnabled()
    expect(screen.queryByText(/you hold/i)).not.toBeInTheDocument()
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
