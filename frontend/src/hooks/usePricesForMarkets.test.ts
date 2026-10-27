import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeSocket, latestSocket } from '../test/fakeSocket'
import { holdUntilReleased } from '../test/holdUntilReleased'
import { server } from '../test/server'
import { PRICE_REFRESH_MS, usePricesForMarkets } from './usePricesForMarkets'

const LEDGER_BASE = 'http://localhost:8003'
const BATCH = `${LEDGER_BASE}/ledger/markets/snapshots`
const SNAPSHOT = `${LEDGER_BASE}/ledger/markets/:id/snapshot`

// One array for every render: a new array each time would be a new list.
const TRADING = [{ id: 'm1', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' }]
// Its status column still reads open, and the clock says it closed an hour
// ago. ADR 0011: the clock is the authority.
const PAST_ITS_CLOSE = [{ id: 'm1', status: 'open' as const, close_time: '2020-01-01T00:00:00Z' }]

// A book that has moved reports a higher `state_version`, and the version is
// what decides whether an answer is newer (realtime-service.md). A fixture
// that held it constant would have every refresh dropped as stale.
function snapshotPriced(price: string, marketId = 'm1', version = 1) {
  return {
    market_id: marketId,
    state_version: version,
    prices: [{ outcome_id: 'o1', position: 0, price }],
    occurred_at: '2026-10-18T00:00:00Z',
    credits_traded: '0.0000',
  }
}

// Answers each batch request with the next price, and counts the requests.
function serveChangingPrices(prices: string[]) {
  const reads = { count: 0 }
  server.use(
    http.post(BATCH, async ({ request }) => {
      const { market_ids: ids } = (await request.json()) as { market_ids: string[] }
      const round = Math.min(reads.count, prices.length - 1)
      const price = prices[round]
      reads.count += 1
      return HttpResponse.json({ snapshots: ids.map(id => snapshotPriced(price, id, round + 1)) })
    }),
  )
  return reads
}

// Opens the socket, if it is not open already, and acks every subscribe it has
// not answered yet. Opening twice would re-run `onopen`, which re-subscribes
// from scratch — a real reconnect does exactly that, and a test that wants one
// closes the socket instead.
const acked = new Set<string>()
async function connectAndAck() {
  const socket = latestSocket()
  if (socket.readyState !== 1) await act(async () => { socket.open() })
  const waiting = socket.sent
    .filter(command => command.action === 'subscribe')
    .map(command => command.market_id)
    .filter(id => !acked.has(id))
  await act(async () => {
    for (const id of waiting) {
      acked.add(id)
      socket.receive({ type: 'subscribed', market_id: id })
    }
  })
}


// Puts the hook's markets on the polled side of the divide: the socket refuses
// the subscription, so the timer is what keeps them fresh. The refusal frame
// names no market, so one arrives per outstanding subscribe.
async function refuseEverySubscription() {
  await act(async () => { latestSocket().open() })
  const outstanding = latestSocket().sent.filter(command => command.action === 'subscribe').length
  await act(async () => {
    for (let i = 0; i < outstanding; i += 1) {
      latestSocket().receive({
        type: 'error',
        error: { code: 'too_many_subscriptions', message: 'at its ceiling' },
      })
    }
  })
}

// A relay that answers the socket's commands in the order they were sent and
// refuses a subscribe past its ceiling, as the real one does
// (realtime_service/service/subscriptions.py). Each call answers what was
// sent since the last one and returns the markets the relay now holds.
function relayWithCeiling(ceiling: number) {
  const held = new Set<string>()
  let answered = 0
  return async () => {
    const socket = latestSocket()
    const commands = socket.sent.slice(answered)
    answered = socket.sent.length
    await act(async () => {
      for (const { action, market_id: id } of commands) {
        if (action === 'unsubscribe') {
          held.delete(id)
          socket.receive({ type: 'unsubscribed', market_id: id })
        } else if (!held.has(id) && held.size >= ceiling) {
          socket.receive({ type: 'error', error: { code: 'too_many_subscriptions', message: 'at its ceiling' } })
        } else {
          held.add(id)
          socket.receive({ type: 'subscribed', market_id: id })
        }
      }
    })
    return held
  }
}

// Markets m<from> to m<to - 1>, all still trading.
function marketsNumbered(from: number, to: number) {
  return Array.from({ length: to - from }, (_, index) => ({
    id: `m${from + index}`,
    status: 'open' as const,
    close_time: '2099-01-05T12:00:00Z',
  }))
}

const subscribedTo = () =>
  latestSocket().sent.filter(c => c.action === 'subscribe').map(c => c.market_id)
const unsubscribedFrom = () =>
  latestSocket().sent.filter(c => c.action === 'unsubscribe').map(c => c.market_id)

describe('usePricesForMarkets', () => {
  beforeEach(() => {
    FakeSocket.instances = []
    acked.clear()
    // Stubbed per test: MSW installs its own WebSocket when the server starts.
    vi.stubGlobal('WebSocket', FakeSocket)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  // #104 amendment D: the hook skips markets it already has prices for, and
  // StrictMode (on in main.tsx) mounts it, cancels that first request and
  // mounts it again. A market counted as fetched when its request went out,
  // rather than when its answer landed, would never be asked for again and
  // its card would show "—" for good.
  it('still prices every market when StrictMode cancels the first mount', async () => {
    serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING), { wrapper: StrictMode })

    // The whole snapshot, not only its prices: a card needs state_version to
    // tell an untraded book from a priced one, and occurred_at to say when it
    // last moved (#445).
    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')).toEqual(snapshotPriced('0.6000')))
  })

  // [X-1] #432: one load of this page opened one snapshot request per card.
  it('reads the whole page in one request, not one per card', async () => {
    const pageOfThree = [
      { id: 'm1', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
      { id: 'm2', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
      { id: 'm3', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
    ]
    const asked: string[][] = []
    server.use(
      http.post(BATCH, async ({ request }) => {
        const { market_ids: ids } = (await request.json()) as { market_ids: string[] }
        asked.push(ids)
        return HttpResponse.json({ snapshots: ids.map(id => snapshotPriced('0.6000', id)) })
      }),
    )

    const { result } = renderHook(() => usePricesForMarkets(pageOfThree))

    await waitFor(() => expect(result.current.snapshotsByMarket.size).toBe(3))
    expect(asked).toHaveLength(1)
    expect([...asked[0]].sort()).toEqual(['m1', 'm2', 'm3'])
  })

  // [X-1] #432 and #444 AC 4: the acks for a page arrive as one frame per
  // market, and each is a reason to read that market again. Read one at a
  // time, a page of fifty is fifty-one batch requests on every load and every
  // reconnect — the per-card round #432 removed — so the acks are read
  // together once the socket has heard back about every subscribe it sent.
  it('reads the markets a burst of acks names in one request, not one per ack', async () => {
    const asked: string[][] = []
    server.use(http.post(BATCH, async ({ request }) => {
      const { market_ids: ids } = (await request.json()) as { market_ids: string[] }
      asked.push(ids)
      return HttpResponse.json({ snapshots: ids.map(id => snapshotPriced('0.6000', id)) })
    }))
    const pageOfThree = marketsNumbered(1, 4)

    const { result } = renderHook(() => usePricesForMarkets(pageOfThree))
    await waitFor(() => expect(result.current.snapshotsByMarket.size).toBe(3))
    await connectAndAck()
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })

    expect(asked).toHaveLength(2)
    expect([...asked[1]].sort()).toEqual(['m1', 'm2', 'm3'])
  })

  // [X-1] #444 AC 4: the snapshot is first paint for every card, not only for
  // the fifty the socket follows. A card past the follow cap that waited for
  // the timer would say "Loading prices…" for half a minute.
  it('prices every card on first paint, past the markets the socket follows', async () => {
    serveChangingPrices(['0.6000'])
    const fiftyOne = marketsNumbered(0, 51)

    const { result } = renderHook(() => usePricesForMarkets(fiftyOne))

    await waitFor(() => expect(result.current.snapshotsByMarket.size).toBe(51))
  })

  // The batch route takes at most 200 ids and refuses a 201st with a 422
  // (ledger-service.md). Load more appends past that, and every market the
  // socket is not carrying is read on the timer, so a long list has to go as
  // several requests or every one of its reads fails.
  it('splits a read of more than 200 markets into requests the batch route accepts', async () => {
    const asked: string[][] = []
    server.use(http.post(BATCH, async ({ request }) => {
      const { market_ids: ids } = (await request.json()) as { market_ids: string[] }
      asked.push(ids)
      return HttpResponse.json({ snapshots: ids.map(id => snapshotPriced('0.6000', id)) })
    }))
    // Fifty followed, and 201 past the cap: one read of those would be a 201.
    const longList = marketsNumbered(0, 251)

    const { result } = renderHook(() => usePricesForMarkets(longList))

    await waitFor(() => expect(result.current.snapshotsByMarket.size).toBe(251))
    expect(Math.max(...asked.map(ids => ids.length))).toBeLessThanOrEqual(200)
  })

  // [X-1] #444 AC 1: one socket per page, following every market rendered.
  it('subscribes every rendered market on one socket', async () => {
    serveChangingPrices(['0.6000'])
    const twoMarkets = [
      { id: 'm1', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
      { id: 'm2', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
    ]

    renderHook(() => usePricesForMarkets(twoMarkets))
    await connectAndAck()

    expect(FakeSocket.instances).toHaveLength(1)
    expect([...subscribedTo()].sort()).toEqual(['m1', 'm2'])
  })

  // [X-1] #444 AC 1: "Load more" appends, and the cards it adds are followed
  // too — without re-subscribing the ones already on screen, which would spend
  // the ceiling on markets this connection already holds.
  it('subscribes the markets Load more appends, and only those', async () => {
    serveChangingPrices(['0.6000'])
    const firstPage = [{ id: 'm1', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' }]
    const bothPages = [
      ...firstPage,
      { id: 'm2', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
    ]

    const { rerender } = renderHook(({ markets }) => usePricesForMarkets(markets), {
      initialProps: { markets: firstPage },
    })
    await connectAndAck()
    expect(subscribedTo()).toEqual(['m1'])

    rerender({ markets: bothPages })
    await connectAndAck()

    expect(subscribedTo()).toEqual(['m1', 'm2'])
  })

  // [X-1] #444 AC 1 and AC 2: a page of fifty already holds the relay's
  // default ceiling, and Load more moves the followed set onto the fifty it
  // appended. The relay answers in order, so the cards that left have to be
  // unsubscribed before the new ones ask, or every new subscribe is refused
  // and the socket ends up carrying nothing.
  it('keeps the cards Load more appends live when the page already holds the ceiling', async () => {
    serveChangingPrices(['0.6000'])
    const firstPage = marketsNumbered(0, 50)
    const twoPages = [...firstPage, ...marketsNumbered(50, 100)]
    const relay = relayWithCeiling(50)

    const { rerender } = renderHook(({ markets }) => usePricesForMarkets(markets), {
      initialProps: { markets: firstPage },
    })
    await act(async () => { latestSocket().open() })
    await relay()

    rerender({ markets: twoPages })
    const held = await relay()

    expect([...held].sort()).toEqual(twoPages.slice(50).map(market => market.id).sort())
  })

  // [X-1] #444 AC 2: the page asks the socket for at most fifty markets, the
  // relay's default ceiling, and the newest win — the ones a trader just asked
  // for with Load more. The rest are read on the timer.
  it('follows the fifty newest cards when the page holds more', async () => {
    serveChangingPrices(['0.6000'])
    const fiftyOne = marketsNumbered(0, 51)

    renderHook(() => usePricesForMarkets(fiftyOne))
    await act(async () => { latestSocket().open() })

    expect(subscribedTo()).toHaveLength(50)
    expect(subscribedTo()).not.toContain('m0')
  })

  // The refusal frame names no market, so the hook matches it to the oldest
  // subscribe still waiting for an answer. A subscribe already acked is not
  // waiting, and must not be the one blamed.
  it('falls back for the market refused, not the one acked before it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const polled: string[][] = []
    server.use(http.post(BATCH, async ({ request }) => {
      const { market_ids: ids } = (await request.json()) as { market_ids: string[] }
      polled.push(ids)
      return HttpResponse.json({ snapshots: ids.map(id => snapshotPriced('0.6000', id)) })
    }))
    const twoMarkets = marketsNumbered(1, 3)

    const { result } = renderHook(() => usePricesForMarkets(twoMarkets))
    await waitFor(() => expect(result.current.snapshotsByMarket.size).toBe(2))
    await act(async () => { latestSocket().open() })
    await act(async () => {
      latestSocket().receive({ type: 'subscribed', market_id: 'm1' })
      latestSocket().receive({ type: 'error', error: { code: 'too_many_subscriptions', message: 'at its ceiling' } })
    })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })
    polled.length = 0

    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })

    await waitFor(() => expect(polled).toHaveLength(1))
    expect(polled[0]).toEqual(['m2'])
  })

  // [X-1] #444 AC 1: a card that leaves the page stops being followed. The
  // ceiling counts markets, so an unsubscribe is what makes room for the next
  // page.
  it('unsubscribes a market that leaves the rendered set', async () => {
    serveChangingPrices(['0.6000'])
    const bothMarkets = [
      { id: 'm1', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
      { id: 'm2', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' },
    ]
    const onlyFirst = [bothMarkets[0]]

    const { rerender } = renderHook(({ markets }) => usePricesForMarkets(markets), {
      initialProps: { markets: bothMarkets },
    })
    await connectAndAck()

    rerender({ markets: onlyFirst })
    await act(async () => {})

    expect(unsubscribedFrom()).toEqual(['m2'])
  })

  // [X-1] #444 AC 2: the page size and the ceiling are configured in different
  // services and can drift, and "Load more" appends past it either way. A
  // market the socket refuses is read on the timer instead, so nothing on
  // screen is left silently frozen.
  it('falls back to reading a market the socket refuses to follow', async () => {
    const reads = serveChangingPrices(['0.6000', '0.7000'])
    vi.useFakeTimers({ shouldAdvanceTime: true })

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await act(async () => { latestSocket().open() })
    await act(async () => {
      latestSocket().receive({
        type: 'error',
        error: { code: 'too_many_subscriptions', message: 'at its ceiling' },
      })
    })

    const before = reads.count
    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })
    await waitFor(() => expect(reads.count).toBeGreaterThan(before))
    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.7000'))
  })

  // A refusal belongs to the connection that gave it: the ceiling counts one
  // connection's own subscriptions, so a reconnect starts with room. Held past
  // the drop, a market refused once would be polled for the life of the page
  // while the new connection pushed its frames — both at once, and the poll is
  // what #444 exists to remove.
  it('follows a refused market again after a reconnect', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const reads = serveChangingPrices(['0.6000', '0.7000', '0.8800'])

    renderHook(() => usePricesForMarkets(TRADING))
    await act(async () => { latestSocket().open() })
    await act(async () => {
      latestSocket().receive({
        type: 'error',
        error: { code: 'too_many_subscriptions', message: 'at its ceiling' },
      })
    })
    // Refused, so the timer is carrying it.
    const whileRefused = reads.count
    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })
    await waitFor(() => expect(reads.count).toBeGreaterThan(whileRefused))

    // The connection drops and the next one has room.
    await act(async () => { latestSocket().serverClose(1006) })
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 3000 })
    acked.clear()
    await connectAndAck()

    // Followed again, so the timer lets it go: the socket is carrying it now.
    const whenFollowedAgain = reads.count
    await act(async () => { vi.advanceTimersByTime(3 * PRICE_REFRESH_MS) })

    expect(reads.count).toBe(whenFollowedAgain)
  })

  // [X-1] #444: a market the relay is pushing needs no poll, and polling it
  // anyway costs the ledger an LMSR evaluation per card twice a minute for a
  // price it has already sent. The thirty-second re-read was the old way a
  // price arrived; now it serves only what the socket is not carrying.
  it('stops polling a market the socket is following', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const reads = serveChangingPrices(['0.6000'])

    renderHook(() => usePricesForMarkets(TRADING))
    await waitFor(() => expect(reads.count).toBe(1))
    await connectAndAck()
    const afterSubscribe = reads.count

    await act(async () => { vi.advanceTimersByTime(3 * PRICE_REFRESH_MS) })

    expect(reads.count).toBe(afterSubscribe)
  })

  // [X-1] #444 AC 5: a socket that has dropped carries nothing, so the markets
  // it was following go back on the timer until it is connected again. A
  // market counted as pushed while nothing can push it is the silently frozen
  // list the criterion rules out.
  it('keeps reading the prices on the timer while the socket is away', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const reads = serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await connectAndAck()
    await act(async () => { latestSocket().serverClose(1006) })
    await waitFor(() => expect(result.current.status).toBe('reconnecting'))
    const whileAway = reads.count

    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })

    await waitFor(() => expect(reads.count).toBeGreaterThan(whileAway))
  })

  // A browser socket cannot send a fresh token, so when the relay closes for
  // an expired one (4408) something has to go through the axios interceptor
  // before the reconnect, or every retry is refused the same way. The detail
  // page does this already; the list follows markets on the same socket.
  it('refreshes the session before reconnecting when the token expired', async () => {
    serveChangingPrices(['0.6000'])
    let singleReads = 0
    server.use(http.get(SNAPSHOT, () => {
      singleReads += 1
      return HttpResponse.json(snapshotPriced('0.6000'))
    }))

    renderHook(() => usePricesForMarkets(TRADING))
    await connectAndAck()
    await act(async () => { latestSocket().serverClose(4408) })
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 3000 })

    expect(singleReads).toBe(1)
  })

  // [X-1] #444 AC 1 and AC 3: a price frame repaints the card it names, and
  // nothing else, without waiting for the timer.
  it('applies a price frame to the market it names', async () => {
    serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await connectAndAck()
    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.6000'))

    await act(async () => {
      latestSocket().receive({
        type: 'price',
        market_id: 'm1',
        state_version: 9,
        prices: [{ outcome_id: 'o1', position: 0, price: '0.8100' }],
        occurred_at: '2026-10-18T01:00:00Z',
      })
    })

    expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.8100')
  })

  // realtime-service.md, "`state_version`, and why stale events cannot win":
  // a frame at or below what is on screen is dropped. Replicas keep separate
  // counts, so this is a frame that can really arrive.
  it('drops a price frame no newer than what is on screen', async () => {
    serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await connectAndAck()
    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.6000'))

    await act(async () => {
      latestSocket().receive({
        type: 'price',
        market_id: 'm1',
        state_version: 1,
        prices: [{ outcome_id: 'o1', position: 0, price: '0.1000' }],
        occurred_at: '2026-10-18T01:00:00Z',
      })
    })

    expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.6000')
  })

  // [X-1] #444 AC 4 and [X-4] #37: events published while the socket was away
  // are gone — Redis pub/sub buffers nothing — so a reconnect re-reads rather
  // than resuming from whatever it last saw.
  it('re-reads the prices after a reconnect', async () => {
    // A fresh price per read, so the one the reconnect fetches is visibly not
    // the one already on screen.
    const reads = serveChangingPrices(['0.6000', '0.7000', '0.8800'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await connectAndAck()
    await waitFor(() => expect(result.current.snapshotsByMarket.has('m1')).toBe(true))
    const priceBefore = result.current.snapshotsByMarket.get('m1')?.prices[0].price
    const readsBefore = reads.count

    await act(async () => { latestSocket().serverClose(1006) })
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 3000 })
    acked.clear()
    await connectAndAck()

    // The read is what matters: events published while the socket was away are
    // gone, so the reconnect has to ask rather than resume.
    await waitFor(() => expect(reads.count).toBeGreaterThan(readsBefore))
    await waitFor(() =>
      expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).not.toBe(priceBefore),
    )
  })

  // [X-1] #444 AC 5: the list says the feed is in trouble rather than showing
  // a frozen price that looks as authoritative as a fresh one.
  it('reports the connection state the list has to show', async () => {
    serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    expect(result.current.status).toBe('connecting')

    await connectAndAck()
    expect(result.current.status).toBe('connected')

    // 4403 is the origin check refusing the handshake: retrying cannot help.
    await act(async () => { latestSocket().serverClose(4403) })
    await waitFor(() => expect(result.current.status).toBe('degraded'))
    // And the price it already had stays on screen (AC 5).
    expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.6000')
  })

  // ledger-service.md: the batch route opens no book, so a published market
  // nobody has previewed or traded is absent from its answer rather than
  // refused. The single snapshot is what opens one, and the card still needs
  // a price.
  it('asks the single snapshot for a market the batch left out', async () => {
    let singleReads = 0
    server.use(
      http.post(BATCH, () => HttpResponse.json({ snapshots: [] })),
      http.get(SNAPSHOT, () => {
        singleReads += 1
        return HttpResponse.json(snapshotPriced('0.5000'))
      }),
    )

    const { result } = renderHook(() => usePricesForMarkets(TRADING))

    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.5000'))
    expect(singleReads).toBe(1)
  })

  // Asked once per market per page: a market the batch keeps leaving out —
  // an unknown id reads the same way — is not opened again on every tick.
  it('asks the single snapshot only once for a market the batch keeps leaving out', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let batchReads = 0
    let singleReads = 0
    server.use(
      http.post(BATCH, () => {
        batchReads += 1
        return HttpResponse.json({ snapshots: [] })
      }),
      http.get(SNAPSHOT, () => {
        singleReads += 1
        return HttpResponse.json(snapshotPriced('0.5000'))
      }),
    )

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await waitFor(() => expect(result.current.snapshotsByMarket.has('m1')).toBe(true))
    for (const tick of [2, 3]) {
      await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })
      await waitFor(() => expect(batchReads).toBe(tick))
    }
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })

    expect(singleReads).toBe(1)
  })

  // #104 amendment D, for the market the batch leaves out. StrictMode's
  // discarded first mount sends its own batch read; if that answer marks the
  // market's book as opened, the single read it starts lands on the discarded
  // mount, and the remount's answer must not then skip the market as done.
  it('still prices a market the batch left out when StrictMode cancels the first mount', async () => {
    server.use(
      http.post(BATCH, () => HttpResponse.json({ snapshots: [] })),
      http.get(SNAPSHOT, () => HttpResponse.json(snapshotPriced('0.5000'))),
    )

    const { result } = renderHook(() => usePricesForMarkets(TRADING), { wrapper: StrictMode })

    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')?.prices[0].price).toBe('0.5000'))
  })

  // The timer serves the markets the socket is not carrying — past the
  // ceiling, or refused. A followed market gets its updates pushed instead,
  // which is what #444 changed.
  it('reads the prices again on a timer, without a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serveChangingPrices(['0.6000', '0.7000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    const priceNow = () => result.current.snapshotsByMarket.get('m1')?.prices[0].price
    await waitFor(() => expect(priceNow()).toBe('0.6000'))
    await refuseEverySubscription()
    const firstReadAt = result.current.readAt?.getTime() ?? 0

    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })

    await waitFor(() => expect(priceNow()).toBe('0.7000'))
    // The screen reports when the prices were read, so the refresh has to move it.
    expect(result.current.readAt?.getTime()).toBeGreaterThan(firstReadAt)
  })

  // The screen says when the prices were last read, and that is only worth
  // saying if a failing refresh leaves it where it was: a fresh time over
  // prices nobody could re-read is the state this is meant to make visible.
  it('leaves the reported read time alone when a refresh fails', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    const priceNow = () => result.current.snapshotsByMarket.get('m1')?.prices[0].price
    await waitFor(() => expect(priceNow()).toBe('0.6000'))
    await refuseEverySubscription()
    const lastGoodRead = result.current.readAt?.getTime()

    let failedReads = 0
    server.use(http.post(BATCH, () => {
      failedReads += 1
      return HttpResponse.error()
    }))
    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })
    await waitFor(() => expect(failedReads).toBe(1))
    // Long enough for that failure to have landed, if it is going to move anything.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)) })

    expect(result.current.readAt?.getTime()).toBe(lastGoodRead)
    // And the card keeps the price it had rather than blanking.
    expect(priceNow()).toBe('0.6000')
  })

  // A tick that fired while the ledger was still answering the last one would
  // queue a second read, and a slow ledger — several books opening for the
  // first time — is exactly when that happens. Same reason LoadMoreControl
  // disables its button while a page is on its way.
  it('does not ask again about a market it is still waiting on', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { held, release } = holdUntilReleased()
    let reads = 0
    server.use(http.post(BATCH, async ({ request }) => {
      const { market_ids: ids } = (await request.json()) as { market_ids: string[] }
      reads += 1
      await held
      return HttpResponse.json({ snapshots: ids.map(id => snapshotPriced('0.6000', id)) })
    }))

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await waitFor(() => expect(reads).toBe(1))
    await refuseEverySubscription()

    await act(async () => { vi.advanceTimersByTime(3 * PRICE_REFRESH_MS) })

    expect(reads).toBe(1)

    // And the market is readable again once the ledger has answered.
    release()
    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')?.prices).toHaveLength(1))
    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })
    await waitFor(() => expect(reads).toBe(2))
  })

  // [X-1] #431 AC 1: a card showed the same dash for a read still out and a
  // read that failed, so the hook has to say which. A market that has stopped
  // trading is never read again on the timer, so the retry is the only way
  // back to a price for exactly the cards that need one.
  it('reports a market whose read failed, and reads it again when retried', async () => {
    let failing = true
    server.use(
      http.post(BATCH, () => (failing ? HttpResponse.error() : HttpResponse.json({ snapshots: [snapshotPriced('0.6000')] }))),
      http.get(SNAPSHOT, () =>
        failing ? HttpResponse.error() : HttpResponse.json(snapshotPriced('0.6000')),
      ),
    )

    const { result } = renderHook(() => usePricesForMarkets(PAST_ITS_CLOSE))
    await waitFor(() => expect(result.current.failedMarkets.has('m1')).toBe(true))
    expect(result.current.snapshotsByMarket.has('m1')).toBe(false)

    failing = false
    act(() => result.current.retryMarket('m1'))

    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')).toEqual(snapshotPriced('0.6000')))
    // And the card stops offering to retry something that has just answered.
    expect(result.current.failedMarkets.has('m1')).toBe(false)
  })

  // A retry that fails too has to leave the card where it was, saying so and
  // still offering the way back, rather than waiting for a price for good.
  it('puts the failure back when the retry fails as well', async () => {
    let singleReads = 0
    server.use(
      http.post(BATCH, () => HttpResponse.error()),
      http.get(SNAPSHOT, () => {
        singleReads += 1
        return HttpResponse.error()
      }),
    )

    const { result } = renderHook(() => usePricesForMarkets(PAST_ITS_CLOSE))
    await waitFor(() => expect(result.current.failedMarkets.has('m1')).toBe(true))

    act(() => result.current.retryMarket('m1'))

    await waitFor(() => expect(singleReads).toBeGreaterThan(0))
    await waitFor(() => expect(result.current.failedMarkets.has('m1')).toBe(true))
  })

  // A market that can no longer trade can no longer get a new price, and each
  // read costs the ledger an LMSR evaluation.
  it('stops reading a market whose close time has passed, whatever its status says', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const reads = serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(PAST_ITS_CLOSE))
    await waitFor(() => expect(result.current.snapshotsByMarket.get('m1')?.prices).toHaveLength(1))

    await act(async () => { vi.advanceTimersByTime(3 * PRICE_REFRESH_MS) })

    expect(reads.count).toBe(1)
  })
})
