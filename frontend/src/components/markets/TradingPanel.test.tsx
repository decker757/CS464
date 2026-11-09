import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest'
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

// The two refusals that mean the price moved (#589, #644), as the ledger sends them.
const PRICE_MOVED_DETAILS = {
  quote_stale: { quoted: 1, current: 2 },
  price_beyond_limit: { limit: '5.1763', current_total: '5.3000', state_version: 2 },
}

// The next trade is refused with `code`, but only once the returned function
// is called, so the test can change the panel while the trade is in flight.
function holdPriceMovedRefusal(code: keyof typeof PRICE_MOVED_DETAILS): () => void {
  let answerTrade: () => void = () => {}
  const tradeAnswered = new Promise<void>(resolve => { answerTrade = resolve })
  server.use(
    http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async () => {
      await tradeAnswered
      return HttpResponse.json({ error: { code, message: 'Refused.', details: PRICE_MOVED_DETAILS[code] } }, { status: 409 })
    }),
  )
  return () => answerTrade()
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
  // Whose trades the panel keeps keys for across a reload (#585).
  traderId?: string
}

function panel({
  balance = '1000.0000', available = balance, heldByOutcome = HELD_PLENTY, reservedSharesByOutcome = {}, outcomeId = 'o-yes',
  refetch = vi.fn(async () => {}), onTraded = vi.fn(), limitOrdersAllowed = false, onOrderPlaced = vi.fn(),
  traderId = 'trader-1',
}: PanelOptions = {}) {
  return (
    <BalanceContext.Provider value={{ balance, available, refetch }}>
      <TradingPanel
        marketId={MARKET_ID}
        traderId={traderId}
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
  localStorage.clear()
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

  describe('price limits (#644)', () => {
    function acceptTrade(record: (body: Record<string, unknown>) => void, total = '-5.1250') {
      server.use(
        http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
          record(await request.json() as Record<string, unknown>)
          return HttpResponse.json({
            transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
            outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total, state_version: 3,
          }, { status: 201 })
        }),
      )
    }

    it('sends a buy with the most it will pay, 1% over the quoted cost, and no minimum', async () => {
      mockPreview(previewResponse())
      let sentBody: Record<string, unknown> | undefined
      acceptTrade(body => { sentBody = body })
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderPanel()
      await typeQuantity(actor, '10')
      await screen.findByLabelText('trade preview')

      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
      await screen.findByText(/bought/i)

      expect(sentBody).toMatchObject({ max_cost: '5.1763', state_version: 1 })
      expect(sentBody).not.toHaveProperty('min_proceeds')
    })

    it('sends a sell with the least it will accept, 1% under the quoted proceeds, and no maximum', async () => {
      mockPreview(previewResponse({ side: 'sell', total: '4.9876' }))
      let sentBody: Record<string, unknown> | undefined
      acceptTrade(body => { sentBody = body }, '4.9876')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderPanel()
      await actor.click(screen.getByRole('button', { name: 'Sell' }))
      await typeQuantity(actor, '10')
      await screen.findByLabelText('trade preview')

      await actor.click(screen.getByRole('button', { name: /^sell yes$/i }))
      await waitFor(() => expect(sentBody).toBeDefined())

      expect(sentBody).toMatchObject({ min_proceeds: '4.9377' })
      expect(sentBody).not.toHaveProperty('max_cost')
    })

    it('shows the limit before the click, on both sides', async () => {
      mockPreview(previewResponse())
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderPanel()
      await typeQuantity(actor, '10')
      expect(await screen.findByLabelText('trade preview')).toHaveTextContent(/pay at most\s*5\.1763 credits/i)

      mockPreview(previewResponse({ side: 'sell', total: '4.9876' }))
      await actor.click(screen.getByRole('button', { name: 'Sell' }))
      await act(() => vi.advanceTimersByTimeAsync(300))
      expect(await screen.findByLabelText('trade preview')).toHaveTextContent(/receive at least\s*4\.9377 credits/i)
    })

    it('confirms what the trade actually cost when the price moved within the limit, not the quote', async () => {
      mockPreview(previewResponse())
      acceptTrade(() => {}, '-5.1500')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderPanel()
      await typeQuantity(actor, '10')
      await screen.findByLabelText('trade preview')

      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

      expect(await screen.findByText(/bought 10\.0000 yes for 5\.1500 credits/i)).toBeInTheDocument()
    })

    it('re-quotes on price_beyond_limit, asks to confirm again, and sends the same key with the new limit', async () => {
      let previewCalls = 0
      server.use(
        http.get(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/preview`, () => {
          previewCalls += 1
          return HttpResponse.json(previewCalls === 1
            ? previewResponse({ state_version: 1 })
            : previewResponse({ state_version: 2, total: '-5.3000' }))
        }),
      )
      const sent: Record<string, unknown>[] = []
      server.use(
        http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
          sent.push(await request.json() as Record<string, unknown>)
          if (sent.length === 1) {
            return HttpResponse.json(
              { error: { code: 'price_beyond_limit', message: 'Beyond.', details: { limit: '5.1763', current_total: '5.3000', state_version: 2 } } },
              { status: 409 },
            )
          }
          return HttpResponse.json({
            transaction_id: 'tx-1', user_id: 'u1', market_id: MARKET_ID,
            outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.3000', state_version: 3,
          }, { status: 201 })
        }),
      )
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderPanel()
      await typeQuantity(actor, '10')
      await screen.findByLabelText('trade preview')

      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
      expect(await screen.findByText(/prices moved\. check the new quote and confirm again/i)).toBeInTheDocument()
      expect(screen.getByLabelText('trade preview')).toHaveTextContent(/pay at most\s*5\.3530 credits/i)

      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
      await screen.findByText(/bought/i)

      expect(sent).toHaveLength(2)
      expect(sent[1].idempotency_key).toBe(sent[0].idempotency_key)
      expect(sent[1]).toMatchObject({ max_cost: '5.3530', state_version: 2 })
    })
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
    // Nothing in place says the trade was not made, so the alert above the
    // tickets does, without asking for a price that is not coming (#589).
    expect(screen.getByText(/^Your buy of 10 Yes was not made: the price moved before it went through\.$/)).toBeInTheDocument()
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
    expect(await screen.findByText(/could not be confirmed/i)).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    // One trade under one key, whichever attempt committed. "Bought" here
    // would tell a trader who meant to buy again that they hold twice what
    // they do (#580).
    expect(await screen.findByText(/^your buy of .* Yes went through once, for 5\.1250 credits\. It was sent more than once, but only one trade was made\.$/i)).toBeInTheDocument()
    expect(screen.queryByText(/^bought /i)).not.toBeInTheDocument()

    expect(attempts).toBe(2)
    expect(keys[0]).toBe(keys[1])
  })

  it('keeps the key for a retry that writes the same quantity differently, which the ledger reads as the same trade', async () => {
    // ledger-service.md compares quantity numerically, so 10 and 10.0 are
    // one trade. A new key here would buy a second time if the first went
    // through.
    mockPreview(previewResponse())
    const keys: unknown[] = []
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>
        keys.push(body.idempotency_key)
        return HttpResponse.error()
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/could not be confirmed/i)

    await typeQuantity(actor, '.0')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await waitFor(() => expect(keys).toHaveLength(2))

    expect(keys[0]).toBe(keys[1])
  })

  it.each(['quote_stale', 'price_beyond_limit'] as const)('says a trade refused as %s was not made, naming it, after the trader picked another outcome', async code => {
    mockPreview(previewResponse())
    const answerTrade = holdPriceMovedRefusal(code)
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    // The No price block, clicked while the Yes buy is still in flight (#589).
    rerender(panel({ outcomeId: 'o-no' }))
    answerTrade()

    expect(await screen.findByRole('alert')).toHaveTextContent(/buy of 10 Yes was not made/i)
  })

  it('says how far past its limit a refused trade went, after the trader picked another outcome', async () => {
    // The interim line with the figures lasts only until the re-quote
    // answers; the alert above the tickets is what stays.
    mockPreview(previewResponse())
    const answerTrade = holdPriceMovedRefusal('price_beyond_limit')
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    rerender(panel({ outcomeId: 'o-no' }))
    answerTrade()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /^Your buy of 10 Yes was not made: the price moved before it went through\. It now comes to 5\.3000 credits, past your limit of 5\.1763\.$/,
    )
  })

  it('says a trade refused as quote_stale was not made after the trader switched to the Limit ticket', async () => {
    mockPreview(previewResponse())
    const answerTrade = holdPriceMovedRefusal('quote_stale')
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ limitOrdersAllowed: true })
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    // The market ticket, where "Prices moved" would show, is not on screen.
    await actor.click(screen.getByRole('button', { name: 'Limit' }))
    answerTrade()

    expect(await screen.findByText(/buy of 10 Yes was not made/i)).toBeInTheDocument()
  })

  it('says a quote_stale refusal once, in place, while the trader is still on that trade', async () => {
    mockPreview(previewResponse())
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
    await screen.findByText(/prices moved\. check the new quote/i)

    expect(screen.getAllByRole('alert')).toHaveLength(1)
  })

  it('says a trade with no answer could not be confirmed, naming it, after the trader picked another outcome', async () => {
    mockPreview(previewResponse())
    let answerTrade: () => void = () => {}
    const tradeAnswered = new Promise<void>(resolve => { answerTrade = resolve })
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async () => {
        await tradeAnswered
        return HttpResponse.error()
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    rerender(panel({ outcomeId: 'o-no' }))
    answerTrade()

    expect(await screen.findByRole('alert')).toHaveTextContent(/buy of 10 Yes could not be confirmed/i)
  })

  it('says a buy with no answer could not be confirmed after the trader switched to Sell on an outcome they hold none of', async () => {
    mockPreview(previewResponse())
    let answerTrade: () => void = () => {}
    const tradeAnswered = new Promise<void>(resolve => { answerTrade = resolve })
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async () => {
        await tradeAnswered
        return HttpResponse.error()
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel({ heldByOutcome: {} })
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')

    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await actor.click(screen.getByRole('button', { name: 'Sell' }))
    answerTrade()

    expect(await screen.findByRole('alert')).toHaveTextContent(/buy of 10 Yes could not be confirmed/i)
  })

  it('sends a sell of the same outcome and quantity with a new key, not the one a buy with no answer still holds', async () => {
    mockPreview(previewResponse())
    const sent: Record<string, unknown>[] = []
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        sent.push(await request.json() as Record<string, unknown>)
        return HttpResponse.error()
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/could not be confirmed/i)

    await actor.click(screen.getByRole('button', { name: 'Sell' }))
    await act(() => vi.advanceTimersByTimeAsync(300))
    await actor.click(await screen.findByRole('button', { name: /^sell yes$/i }))
    await waitFor(() => expect(sent).toHaveLength(2))

    expect(sent[1]).toMatchObject({ outcome_id: 'o-yes', side: 'sell' })
    expect(sent[1].idempotency_key).not.toBe(sent[0].idempotency_key)
  })

  it('retries a buy with no answer under its own key, even after another trade went through in between', async () => {
    // The message tells the trader that sending the same trade again will not
    // make it twice. That has to hold whatever they traded since, or a buy
    // that did commit is bought a second time under a new key.
    mockPreview(previewResponse())
    const sent: Record<string, unknown>[] = []
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        const body = await request.json() as Record<string, unknown>
        sent.push(body)
        if (body.outcome_id === 'o-no') {
          return HttpResponse.json({
            transaction_id: 'tx-no', user_id: 'u1', market_id: MARKET_ID,
            outcome_id: 'o-no', side: 'buy', quantity: '5.0000', total: '-2.5000', state_version: 2,
          }, { status: 201 })
        }
        return HttpResponse.error()
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/could not be confirmed/i)

    rerender(panel({ outcomeId: 'o-no' }))
    await actor.clear(screen.getByLabelText('Quantity (shares)'))
    await typeQuantity(actor, '5')
    await actor.click(await screen.findByRole('button', { name: /^buy no$/i }))
    await screen.findByText(/bought/i)

    rerender(panel({ outcomeId: 'o-yes' }))
    await typeQuantity(actor, '10')
    await actor.click(await screen.findByRole('button', { name: /^buy yes$/i }))
    await waitFor(() => expect(sent).toHaveLength(3))

    expect(sent[2]).toMatchObject({ outcome_id: 'o-yes', quantity: '10' })
    expect(sent[2].idempotency_key).toBe(sent[0].idempotency_key)
  })

  it('sends a trade on another outcome with a new key, not the one a trade with no answer still holds', async () => {
    mockPreview(previewResponse())
    const sent: Record<string, unknown>[] = []
    server.use(
      http.post(`${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`, async ({ request }) => {
        sent.push(await request.json() as Record<string, unknown>)
        return HttpResponse.error()
      }),
    )
    const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { rerender } = renderPanel()
    await typeQuantity(actor, '10')
    await screen.findByLabelText('trade preview')
    await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    await screen.findByText(/could not be confirmed/i)

    rerender(panel({ outcomeId: 'o-no' }))
    await act(() => vi.advanceTimersByTimeAsync(300))
    await actor.click(await screen.findByRole('button', { name: /^buy no$/i }))
    await waitFor(() => expect(sent).toHaveLength(2))

    expect(sent[1]).toMatchObject({ outcome_id: 'o-no' })
    expect(sent[1].idempotency_key).not.toBe(sent[0].idempotency_key)
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
    expect(await screen.findByText(/went through once/i)).toBeInTheDocument()

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

  describe('an unconfirmed trade across a reload (#585)', () => {
    const TRADES = `${LEDGER_BASE}/ledger/markets/${MARKET_ID}/trades`
    const BY_KEY = `${TRADES}/by-key`
    const BOUGHT_10_YES = {
      transaction_id: 'tx-1', user_id: 'trader-1', market_id: MARKET_ID,
      outcome_id: 'o-yes', side: 'buy', quantity: '10.0000', total: '-5.1250', state_version: 2, book_state_version: 2,
    }

    type Lookup = 'found' | 'found-another' | 'not-found' | 'unavailable' | 'never'

    /** Every trade the panel sends, answered by `answer`. */
    function recordTrades(answer: () => Response | Promise<Response>) {
      const sent: Record<string, unknown>[] = []
      server.use(
        http.post(TRADES, async ({ request }) => {
          sent.push(await request.json() as Record<string, unknown>)
          return answer()
        }),
      )
      return sent
    }

    /** Every key the panel asks the ledger about, answered in turn by
     *  `answers`, the last one repeating. */
    function answerLookups(...answers: Lookup[]) {
      const asked: string[] = []
      server.use(
        http.get(BY_KEY, async ({ request }) => {
          asked.push(new URL(request.url).searchParams.get('idempotency_key') ?? '')
          const answer = answers[Math.min(asked.length, answers.length) - 1]
          if (answer === 'never') await delay('infinite')
          if (answer === 'found') return HttpResponse.json(BOUGHT_10_YES)
          if (answer === 'found-another') return HttpResponse.json({ ...BOUGHT_10_YES, outcome_id: 'o-no' })
          if (answer === 'unavailable') return HttpResponse.json({ error: { code: 'internal', message: 'down' } }, { status: 503 })
          return HttpResponse.json({ error: { code: 'trade_not_found', message: 'No trade was made under this key.' } }, { status: 404 })
        }),
      )
      return asked
    }

    const noAnswer = () => HttpResponse.error()
    const created = () => HttpResponse.json(BOUGHT_10_YES, { status: 201 })

    async function buyTenYes(actor: ReturnType<typeof userEvent.setup>) {
      await typeQuantity(actor, '10')
      await screen.findByLabelText('trade preview')
      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
    }

    /** A reload, `secondsLater`: the panel and its memory go, the tab's
     *  storage stays. */
    function reload(unmount: () => void, secondsLater: number, options: PanelOptions = {}) {
      unmount()
      vi.setSystemTime(Date.now() + secondsLater * 1000)
      return renderPanel(options)
    }

    it('after a reload, says a trade with no answer went through, naming it, and a repeat of it is a new trade', async () => {
      mockPreview(previewResponse())
      let answer = noAnswer
      const sent = recordTrades(() => answer())
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      const refetch = vi.fn(async () => {})
      const onTraded = vi.fn()
      reload(unmount, 120, { refetch, onTraded })

      expect(await screen.findByText('Your buy of 10 Yes went through, for 5.1250 credits.')).toBeInTheDocument()
      expect(asked).toEqual([sent[0].idempotency_key])
      expect(screen.queryByText(/could not be confirmed/i)).not.toBeInTheDocument()
      // The balance and holdings were read before the ledger said so.
      expect(refetch).toHaveBeenCalled()
      expect(onTraded).toHaveBeenCalled()

      answer = created
      await buyTenYes(actor)
      await waitFor(() => expect(sent).toHaveLength(2))
      expect(sent[1].idempotency_key).not.toBe(sent[0].idempotency_key)
    })

    it('after a reload, says a trade with no answer did not go through once five minutes have passed since it was sent, and forgets its key', async () => {
      mockPreview(previewResponse())
      let answer = noAnswer
      const sent = recordTrades(() => answer())
      answerLookups('not-found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 301)

      expect(await screen.findByText('Your buy of 10 Yes did not go through.')).toBeInTheDocument()

      answer = created
      await buyTenYes(actor)
      await waitFor(() => expect(sent).toHaveLength(2))
      expect(sent[1].idempotency_key).not.toBe(sent[0].idempotency_key)
    })

    it('does not take a not-found inside five minutes of sending as final: it keeps checking, and says so when the trade commits late', async () => {
      // A trade still queued on the ledger has written nothing yet, so the
      // first answer can be "not found" for a trade that then commits
      // (ledger-service.md, the trade lookup). Saying "did not go through"
      // then would invite the trader to buy it twice.
      mockPreview(previewResponse())
      const sent = recordTrades(noAnswer)
      const asked = answerLookups('not-found', 'found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 10)

      expect(await screen.findByText('Checking whether your buy of 10 Yes went through…')).toBeInTheDocument()
      expect(screen.queryByText(/did not go through/i)).not.toBeInTheDocument()

      await act(() => vi.advanceTimersByTimeAsync(290_000))

      expect(await screen.findByText('Your buy of 10 Yes went through, for 5.1250 credits.')).toBeInTheDocument()
      expect(asked).toEqual([sent[0].idempotency_key, sent[0].idempotency_key])
    })

    it('after a reload, says the trade did not go through when the ledger holds another trade under its key', async () => {
      // A 200 is whatever committed under the key; a different trade sent
      // under it was refused idempotency_key_reused (ledger-service.md).
      mockPreview(previewResponse())
      recordTrades(noAnswer)
      answerLookups('found-another')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 10)

      expect(await screen.findByText('Your buy of 10 Yes did not go through.')).toBeInTheDocument()
    })

    it('after a reload, sends a retry of the trade under its own key until the ledger has answered', async () => {
      mockPreview(previewResponse())
      let answer = noAnswer
      const sent = recordTrades(() => answer())
      answerLookups('never')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 10)
      answer = created
      await buyTenYes(actor)

      await waitFor(() => expect(sent).toHaveLength(2))
      expect(sent[1].idempotency_key).toBe(sent[0].idempotency_key)
      expect(await screen.findByText(/went through once, for 5\.1250 credits\. It was sent more than once/i)).toBeInTheDocument()
    })

    it('after a reload while the trade was still in flight, asks the ledger about it', async () => {
      // The reload is the lost reply: the panel never heard back, and the
      // trade may still have committed.
      mockPreview(previewResponse())
      const sent = recordTrades(async () => {
        await delay('infinite')
        return created()
      })
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await waitFor(() => expect(sent).toHaveLength(1))

      reload(unmount, 120)

      expect(await screen.findByText('Your buy of 10 Yes went through, for 5.1250 credits.')).toBeInTheDocument()
      expect(asked).toEqual([sent[0].idempotency_key])
    })

    it('keeps nothing for a reload for a trade refused because its price moved', async () => {
      mockPreview(previewResponse())
      recordTrades(() => HttpResponse.json(
        { error: { code: 'price_beyond_limit', message: 'Moved.', details: { limit: '5.1763', current_total: '5.3000', state_version: 2 } } },
        { status: 409 },
      ))
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/prices moved/i)

      reload(unmount, 120)
      await act(() => vi.advanceTimersByTimeAsync(1000))

      expect(asked).toEqual([])
    })

    it('counts the wait from the latest attempt: a not-found that crosses a retry in flight does not forget the key', async () => {
      // The trader retries while the panel is asking about the attempt from
      // before the reload. The ledger has not committed the retry yet, so it
      // says not found; measured from the first attempt the wait is long
      // over, and forgetting the key there would send the next click under a
      // new one while the retry may still commit.
      mockPreview(previewResponse())
      const sent = recordTrades(noAnswer)
      let releaseLookup = () => {}
      server.use(
        http.get(BY_KEY, async () => {
          await new Promise<void>(resolve => { releaseLookup = resolve })
          return HttpResponse.json({ error: { code: 'trade_not_found', message: 'No trade.' } }, { status: 404 })
        }),
      )
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 360)
      await screen.findByText(/checking whether your buy of 10 Yes/i)
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)
      releaseLookup()
      await act(() => vi.advanceTimersByTimeAsync(100))

      expect(screen.queryByText(/did not go through/i)).not.toBeInTheDocument()
      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
      await waitFor(() => expect(sent).toHaveLength(3))
      expect(sent[1].idempotency_key).toBe(sent[0].idempotency_key)
      expect(sent[2].idempotency_key).toBe(sent[0].idempotency_key)
    })

    it('does not take a not-found as final while a retry from this page is still out', async () => {
      // The lookup takes no lock and answers at once; the retry may be queued
      // behind the book's lock for longer than the wait. Only once it ends
      // does the wait start.
      mockPreview(previewResponse())
      let releaseRetry = () => {}
      let attempts = 0
      const sent = recordTrades(async () => {
        attempts += 1
        if (attempts === 2) await new Promise<void>(resolve => { releaseRetry = resolve })
        return HttpResponse.error()
      })
      answerLookups('not-found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 10)
      await screen.findByText(/checking whether your buy of 10 Yes/i)
      await buyTenYes(actor)
      await waitFor(() => expect(sent).toHaveLength(2))
      await act(() => vi.advanceTimersByTimeAsync(301_000))
      expect(screen.queryByText(/did not go through/i)).not.toBeInTheDocument()

      releaseRetry()
      expect(await screen.findByText(/could not be confirmed/i)).toBeInTheDocument()
      // The wait runs from the retry's end, not its start, so a re-ask more
      // than five minutes after it was sent, but under five after it ended,
      // is still not final.
      await act(() => vi.advanceTimersByTimeAsync(290_000))
      expect(screen.queryByText(/did not go through/i)).not.toBeInTheDocument()
      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
      await waitFor(() => expect(sent).toHaveLength(3))
      expect(sent[2].idempotency_key).toBe(sent[0].idempotency_key)
    })

    it('stops promising a safe resend once a check has answered for that trade', async () => {
      mockPreview(previewResponse())
      recordTrades(noAnswer)
      answerLookups('not-found', 'found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 10)
      await screen.findByText(/checking whether your buy of 10 Yes/i)
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)
      await act(() => vi.advanceTimersByTimeAsync(301_000))

      expect(await screen.findByText('Your buy of 10 Yes went through, for 5.1250 credits.')).toBeInTheDocument()
      expect(screen.queryByText(/could not be confirmed/i)).not.toBeInTheDocument()
    })

    it('keeps the key for a reload when a retry is refused for its price but an earlier attempt got no answer', async () => {
      // The refusal is about the retry. The first attempt, with its own,
      // looser limit, may still be queued and commit.
      mockPreview(previewResponse())
      let answer: () => Response = noAnswer
      const sent = recordTrades(() => answer())
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)
      answer = () => HttpResponse.json(
        { error: { code: 'price_beyond_limit', message: 'Moved.', details: { limit: '5.1763', current_total: '5.3000', state_version: 2 } } },
        { status: 409 },
      )
      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
      await screen.findByText(/prices moved/i)

      reload(unmount, 120)

      expect(await screen.findByText('Your buy of 10 Yes went through, for 5.1250 credits.')).toBeInTheDocument()
      expect(asked).toEqual([sent[0].idempotency_key])
    })

    /** Buys 10 Yes in one of two panels rendered side by side, as two tabs
     *  on the same market share storage. */
    async function buyTenYesIn(actor: ReturnType<typeof userEvent.setup>, tab: HTMLElement) {
      const scope = within(tab)
      // By role: two panels in one document share the field's id, so its label
      // finds the first tab's.
      await actor.type(scope.getByRole('spinbutton'), '10')
      await act(() => vi.advanceTimersByTimeAsync(300))
      await scope.findByLabelText('trade preview')
      await actor.click(scope.getByRole('button', { name: /^buy yes$/i }))
    }

    /** Web Locks as a browser shares them between tabs; jsdom's has no
     *  query. Removed after the test. It never queues a request, so nothing
     *  here shows a pending request counted, or two tabs' attempts taking
     *  turns: only that a held lock keeps a not-found from being final. */
    function installWebLocks() {
      const held = new Set<string>()
      const locks = {
        async request(name: string, callback: () => Promise<unknown>) {
          held.add(name)
          try {
            return await callback()
          } finally {
            held.delete(name)
          }
        },
        async query() {
          return { held: [...held].map(name => ({ name })), pending: [] }
        },
      }
      const original = Object.getOwnPropertyDescriptor(navigator, 'locks')
      Object.defineProperty(navigator, 'locks', { value: locks, configurable: true })
      onTestFinished(() => {
        if (original) Object.defineProperty(navigator, 'locks', original)
        else Reflect.deleteProperty(navigator, 'locks')
      })
    }

    it('sends a trade under the key another tab kept for it', async () => {
      // The trader had the market open in two tabs: the reply was lost in
      // one, and they buy the same thing in the other.
      mockPreview(previewResponse())
      const sent = recordTrades(noAnswer)
      answerLookups('never')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const tabA = renderPanel().container
      const tabB = renderPanel().container
      await buyTenYesIn(actor, tabA)
      await within(tabA).findByText(/could not be confirmed/i)

      await buyTenYesIn(actor, tabB)

      await waitFor(() => expect(sent).toHaveLength(2))
      expect(sent[1].idempotency_key).toBe(sent[0].idempotency_key)
    })

    it('keeps its promise in a tab that said a resend is safe, after another tab got the answer, and makes the next repeat new', async () => {
      // Tab B said "send the same trade again: it will not be made twice".
      // Tab A then got the answer. B's resend replays the trade under its key
      // and says one trade was made; the click after that is a new trade.
      mockPreview(previewResponse())
      let answer = noAnswer
      const sent = recordTrades(() => answer())
      answerLookups('never')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const tabA = renderPanel().container
      const tabB = renderPanel().container
      await buyTenYesIn(actor, tabA)
      await within(tabA).findByText(/could not be confirmed/i)
      await buyTenYesIn(actor, tabB)
      await within(tabB).findByText(/could not be confirmed/i)

      answer = created
      await actor.click(within(tabA).getByRole('button', { name: /^buy yes$/i }))
      await within(tabA).findByText(/went through once/i)
      await actor.click(within(tabB).getByRole('button', { name: /^buy yes$/i }))
      await within(tabB).findByText(/went through once/i)
      // A confirmed trade clears the field, so the repeat is typed afresh.
      await buyTenYesIn(actor, tabB)

      await waitFor(() => expect(sent).toHaveLength(5))
      expect(sent[2].idempotency_key).toBe(sent[0].idempotency_key)
      expect(sent[3].idempotency_key).toBe(sent[0].idempotency_key)
      expect(sent[4]).toMatchObject({ outcome_id: 'o-yes', quantity: '10' })
      expect(sent[4].idempotency_key).not.toBe(sent[0].idempotency_key)
    })

    it('does not take a not-found as final while another tab\'s attempt under the key is still out', async () => {
      // A duplicated tab, or one opened later, cannot see the other tab's
      // request; the attempt's lock is how it knows the trade may still be
      // queued on the ledger.
      installWebLocks()
      mockPreview(previewResponse())
      let attempts = 0
      const sent = recordTrades(async () => {
        attempts += 1
        if (attempts === 2) await delay('infinite')
        return HttpResponse.error()
      })
      answerLookups('not-found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const tabA = renderPanel().container
      await buyTenYesIn(actor, tabA)
      await within(tabA).findByText(/could not be confirmed/i)
      await actor.click(within(tabA).getByRole('button', { name: /^buy yes$/i }))
      await waitFor(() => expect(sent).toHaveLength(2))
      vi.setSystemTime(Date.now() + 360_000)

      const tabB = renderPanel().container

      expect(await within(tabB).findByText(/checking whether your buy of 10 Yes/i)).toBeInTheDocument()
      await act(() => vi.advanceTimersByTimeAsync(100))
      expect(within(tabB).queryByText(/did not go through/i)).not.toBeInTheDocument()
    })

    it('keeps the key for the page when the browser reads storage but cannot write it', async () => {
      // A full quota, or a private window that refuses writes: the key then
      // lasts as long as the panel, as it did before #585.
      const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new DOMException('quota', 'QuotaExceededError')
      })
      onTestFinished(() => setItem.mockRestore())
      mockPreview(previewResponse())
      const sent = recordTrades(noAnswer)
      answerLookups('never')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))

      await waitFor(() => expect(sent).toHaveLength(2))
      expect(sent[1].idempotency_key).toBe(sent[0].idempotency_key)
    })

    it('stops keeping a trade a day after it was sent', async () => {
      mockPreview(previewResponse())
      recordTrades(noAnswer)
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 25 * 60 * 60)
      await act(() => vi.advanceTimersByTimeAsync(1000))

      expect(asked).toEqual([])
      // Deleted, not only skipped: the trade's description does not stay behind.
      expect(localStorage.length).toBe(0)
    })

    it('deletes a day-old trade on any market, for any trader, when a market loads', async () => {
      // Someone else's trade on a market nobody here opens again: a shared
      // computer must not keep its description for good.
      const trade = { outcomeId: 'o-yes', outcomeLabel: 'Yes', side: 'buy', quantity: '10' }
      const elsewhere = 'unconfirmed-trades:another-trader:another-market'
      const recent = 'unconfirmed-trades:another-trader:a-third-market'
      localStorage.setItem(elsewhere, JSON.stringify([{ key: 'k-old', trade, sentAt: Date.now() - 25 * 60 * 60 * 1000 }]))
      localStorage.setItem(recent, JSON.stringify([{ key: 'k-new', trade, sentAt: Date.now() - 60 * 1000 }]))
      mockPreview(previewResponse())

      renderPanel()
      await act(() => vi.advanceTimersByTimeAsync(1000))

      expect(localStorage.getItem(elsewhere)).toBeNull()
      expect(localStorage.getItem(recent)).not.toBeNull()
    })

    it('keeps nothing for a reload once a retry of the trade was answered', async () => {
      mockPreview(previewResponse())
      let answer = noAnswer
      recordTrades(() => answer())
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)
      answer = created
      await actor.click(screen.getByRole('button', { name: /^buy yes$/i }))
      await screen.findByText(/went through once/i)

      reload(unmount, 120)
      await act(() => vi.advanceTimersByTimeAsync(1000))

      expect(asked).toEqual([])
      expect(screen.queryByText(/went through|did not go through|checking/i)).not.toBeInTheDocument()
    })

    it('keeps nothing for a reload for a trade the ledger refused', async () => {
      mockPreview(previewResponse())
      recordTrades(() => HttpResponse.json(
        { error: { code: 'insufficient_funds', message: 'Not enough credits.', details: { balance: '1.0000', required: '5.1250', reserved: '0.0000', available: '1.0000' } } },
        { status: 409 },
      ))
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/credits/i, { selector: '[role="alert"]' })

      reload(unmount, 120)
      await act(() => vi.advanceTimersByTimeAsync(1000))

      expect(asked).toEqual([])
    })

    it('does not ask about another trader\'s unconfirmed trade after they log out and this trader opens the market', async () => {
      mockPreview(previewResponse())
      recordTrades(noAnswer)
      const asked = answerLookups('found')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel({ traderId: 'trader-1' })
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 120, { traderId: 'trader-2' })
      await act(() => vi.advanceTimersByTimeAsync(1000))

      expect(asked).toEqual([])
      expect(screen.queryByText(/went through/i)).not.toBeInTheDocument()
    })

    it('says the trade could not be confirmed, and keeps its key, when the ledger cannot be asked', async () => {
      mockPreview(previewResponse())
      let answer = noAnswer
      const sent = recordTrades(() => answer())
      answerLookups('unavailable')
      const actor = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      const { unmount } = renderPanel()
      await buyTenYes(actor)
      await screen.findByText(/could not be confirmed/i)

      reload(unmount, 120)

      expect(await screen.findByRole('alert')).toHaveTextContent(/buy of 10 Yes could not be confirmed/i)
      answer = created
      await buyTenYes(actor)
      await waitFor(() => expect(sent).toHaveLength(2))
      expect(sent[1].idempotency_key).toBe(sent[0].idempotency_key)
    })
  })
})
