import { act, configure, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../test/server'
import { useMarketPrices } from './useMarketPrices'

const LEDGER_BASE = 'http://localhost:8003'
const SNAPSHOT = `${LEDGER_BASE}/ledger/markets/:id/snapshot`

// A stand-in for the browser WebSocket that the test drives by hand.
class FakeSocket {
  static OPEN = 1
  static instances: FakeSocket[] = []
  readyState = 0
  sent: { action: string; market_id: string }[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  constructor() { FakeSocket.instances.push(this) }
  send(data: string) { this.sent.push(JSON.parse(data)) }
  // A browser fires `close` asynchronously, for our own close() as well as the server's.
  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    setTimeout(() => this.onclose?.({ code: 1005 }), 0)
  }
  open() { this.readyState = 1; this.onopen?.() }
  receive(msg: object) { this.onmessage?.({ data: JSON.stringify(msg) }) }
  serverClose(code: number) { this.readyState = 3; this.onclose?.({ code }) }
}

const latest = () => FakeSocket.instances[FakeSocket.instances.length - 1]

function priceState(version: number, yes: string, no: string, marketId = 'm1') {
  return {
    market_id: marketId,
    state_version: version,
    // Deliberately out of position order.
    prices: [
      { outcome_id: 'o2', position: 1, price: no },
      { outcome_id: 'o1', position: 0, price: yes },
    ],
    occurred_at: '2026-09-26T00:00:00Z',
  }
}

function subscribe(socket: FakeSocket) {
  act(() => {
    socket.open()
    socket.receive({ type: 'subscribed', market_id: 'm1' })
  })
}

const wait = (ms: number) => act(() => new Promise(resolve => setTimeout(resolve, ms)))

// Counts the snapshot responses MSW sends from now on, so a test can wait for
// a request to have completed rather than for a fixed pause. afterEach removes
// the listener.
function countSnapshotResponses() {
  const counter = { count: 0 }
  server.events.on('response:mocked', ({ request }) => {
    if (new URL(request.url).pathname.endsWith('/snapshot')) counter.count++
  })
  return counter
}

describe('useMarketPrices', () => {
  beforeEach(() => {
    FakeSocket.instances = []
    // Stubbed per test: MSW installs its own WebSocket when the server starts.
    vi.stubGlobal('WebSocket', FakeSocket)
    server.use(http.get(SNAPSHOT, () => HttpResponse.json(priceState(5, '0.6000', '0.4000'))))
  })

  afterEach(() => {
    server.events.removeAllListeners('response:mocked')
    vi.useRealTimers()
    vi.unstubAllGlobals()
    configure({ reactStrictMode: false })
  })

  it('subscribes, and shows the snapshot in position order', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    expect(result.current.status).toBe('connecting')
    subscribe(latest())
    expect(latest().sent).toContainEqual({ action: 'subscribe', market_id: 'm1' })
    await waitFor(() => expect(result.current.prices?.map(p => p.price)).toEqual(['0.6000', '0.4000']))
    expect(result.current.status).toBe('connected')
  })

  it('applies newer price frames and drops ones the snapshot already covers', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))

    act(() => latest().receive({ type: 'price', ...priceState(4, '0.1000', '0.9000') }))
    expect(result.current.prices?.[0].price).toBe('0.6000')

    act(() => latest().receive({ type: 'price', ...priceState(6, '0.7000', '0.3000') }))
    expect(result.current.prices?.[0].price).toBe('0.7000')
  })

  // AC 1 and AC 7: the snapshot comes from the ledger, so a dead socket must
  // not keep the page on "loading price…" while the ledger is healthy.
  it('shows the snapshot when the socket never connects', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(1006))
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))
    expect(result.current.status).toBe('reconnecting')
  })

  // AC 4: an equal version is the same state, and replicas keep separate counts.
  it('drops a frame at the version already on screen', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))

    act(() => latest().receive({ type: 'price', ...priceState(5, '0.1000', '0.9000') }))
    expect(result.current.prices?.[0].price).toBe('0.6000')
  })

  // AC 4: the version only orders events of one market.
  it('drops a frame for a market it is not showing', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))

    act(() => latest().receive({ type: 'price', ...priceState(9, '0.1000', '0.9000', 'm2') }))
    expect(result.current.prices?.[0].price).toBe('0.6000')

    // Still accepts its own market's next frame, so the drop was the market check.
    act(() => latest().receive({ type: 'price', ...priceState(6, '0.7000', '0.3000') }))
    expect(result.current.prices?.[0].price).toBe('0.7000')
  })

  // AC 4: a market nobody has traded is at state_version 0 (DECISIONS.md D-029).
  it("shows a never-traded market's snapshot at version zero", async () => {
    server.use(http.get(SNAPSHOT, () => HttpResponse.json(priceState(0, '0.5000', '0.5000'))))
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.5000'))
  })

  it('keeps the last prices through a reconnect, and a late older frame cannot replace them', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))
    act(() => latest().receive({ type: 'price', ...priceState(6, '0.7000', '0.3000') }))

    act(() => latest().serverClose(1006))
    expect(result.current.status).toBe('reconnecting')
    expect(result.current.prices?.[0].price).toBe('0.7000')

    await wait(1100)
    subscribe(latest())
    // A different replica may still deliver a frame from before the drop.
    act(() => latest().receive({ type: 'price', ...priceState(5, '0.5000', '0.5000') }))
    expect(result.current.prices?.[0].price).toBe('0.7000')
  })

  it('fetches a fresh snapshot after reconnecting, and shows connected even when nothing traded', async () => {
    let snapshots = 0
    server.use(http.get(SNAPSHOT, () => { snapshots++; return HttpResponse.json(priceState(5, '0.6000', '0.4000')) }))
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))

    act(() => latest().serverClose(1006))
    await wait(1100)
    const before = snapshots
    subscribe(latest())
    await waitFor(() => expect(snapshots).toBe(before + 1))
    expect(result.current.status).toBe('connected')
  })

  // AC 5: the snapshot and the socket race after a reconnect, and the newer one wins.
  it('a frame that arrives before the reconnect snapshot resolves is not undone by it', async () => {
    let holdSnapshots = false
    const releases: (() => void)[] = []
    server.use(
      http.get(SNAPSHOT, () => {
        if (!holdSnapshots) return HttpResponse.json(priceState(5, '0.6000', '0.4000'))
        return new Promise(resolve => {
          releases.push(() => resolve(HttpResponse.json(priceState(7, '0.8000', '0.2000'))))
        })
      }),
    )
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))
    act(() => latest().receive({ type: 'price', ...priceState(6, '0.7000', '0.3000') }))

    act(() => latest().serverClose(1006))
    await wait(1100)
    holdSnapshots = true
    subscribe(latest())
    await waitFor(() => expect(releases.length).toBeGreaterThan(0))
    act(() => latest().receive({ type: 'price', ...priceState(8, '0.9000', '0.1000') }))
    expect(result.current.prices?.[0].price).toBe('0.9000')

    const responses = countSnapshotResponses()
    releases.forEach(release => release())
    await waitFor(() => expect(responses.count).toBe(releases.length))
    await wait(0)
    expect(result.current.prices?.[0].price).toBe('0.9000')
    expect(result.current.status).toBe('connected')
  })

  // AC 7: a failed reconcile leaves the last received price on screen.
  it('keeps the last price when the reconnect snapshot fails', async () => {
    let failSnapshots = false
    server.use(
      http.get(SNAPSHOT, () => {
        if (!failSnapshots) return HttpResponse.json(priceState(5, '0.6000', '0.4000'))
        return HttpResponse.json(
          { error: { code: 'market_terms_unavailable', message: 'Market terms are unavailable.' } },
          { status: 503 },
        )
      }),
    )
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))
    act(() => latest().receive({ type: 'price', ...priceState(6, '0.7000', '0.3000') }))

    act(() => latest().serverClose(1006))
    await wait(1100)
    failSnapshots = true
    const responses = countSnapshotResponses()
    subscribe(latest())
    await waitFor(() => expect(responses.count).toBe(1))
    await wait(0)
    expect(result.current.prices?.[0].price).toBe('0.7000')
  })

  it('shows reconnecting for the market now open, not the one before it', async () => {
    // m2's snapshot never arrives, so its prices stay unknown.
    server.use(
      http.get(SNAPSHOT, ({ params }) =>
        params.id === 'm2' ? new Promise<never>(() => {}) : HttpResponse.json(priceState(5, '0.6000', '0.4000')),
      ),
    )
    const { result, rerender } = renderHook(({ id }) => useMarketPrices(id), { initialProps: { id: 'm1' } })
    subscribe(latest())
    await waitFor(() => expect(result.current.prices).not.toBeNull())

    rerender({ id: 'm2' })
    act(() => latest().serverClose(1006))
    expect(result.current.status).toBe('reconnecting')
    expect(result.current.prices).toBeNull()
  })

  it('ignores a failed session refresh for a market the page has already left', async () => {
    const refusals: (() => void)[] = []
    server.use(
      http.get(SNAPSHOT, ({ params }) => {
        if (params.id === 'm2') return HttpResponse.json(priceState(5, '0.6000', '0.4000', 'm2'))
        // Held until the page has moved on to m2.
        return new Promise(resolve => {
          refusals.push(() => resolve(HttpResponse.json({ error: { code: 'invalid_token', message: 'Not authenticated.' } }, { status: 401 })))
        })
      }),
    )
    const { result, rerender } = renderHook(({ id }) => useMarketPrices(id), { initialProps: { id: 'm1' } })
    act(() => latest().serverClose(4401))

    rerender({ id: 'm2' })
    subscribe(latest())
    await waitFor(() => expect(result.current.prices?.[0].price).toBe('0.6000'))

    refusals.forEach(refuse => refuse())
    await wait(50)
    expect(result.current.status).toBe('connected')
    expect(result.current.prices?.[0].price).toBe('0.6000')
  })

  it('does not reconnect in a loop when it closes the socket itself', async () => {
    // StrictMode mounts, unmounts and remounts, closing the first socket. The
    // app runs in StrictMode, so this is every page load in development.
    configure({ reactStrictMode: true })
    const { rerender } = renderHook(({ id }) => useMarketPrices(id), { initialProps: { id: 'm1' } })
    await wait(100)
    expect(FakeSocket.instances.length).toBeLessThanOrEqual(2)

    // Switching market closes the old socket too.
    const before = FakeSocket.instances.length
    rerender({ id: 'm2' })
    await wait(100)
    expect(FakeSocket.instances.length - before).toBeLessThanOrEqual(2)
  })

  it('reconnects after the server closes the connection, after a pause', async () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(1000))
    expect(result.current.status).toBe('reconnecting')
    await act(() => vi.advanceTimersByTimeAsync(900))
    expect(FakeSocket.instances).toHaveLength(1)
    await act(() => vi.advanceTimersByTimeAsync(200))
    expect(FakeSocket.instances).toHaveLength(2)
  })

  it('waits longer after each failed attempt', async () => {
    vi.useFakeTimers()
    renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(1006))
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(FakeSocket.instances).toHaveLength(2)
    act(() => latest().serverClose(1006))
    await act(() => vi.advanceTimersByTimeAsync(1500))
    expect(FakeSocket.instances).toHaveLength(2)
    await act(() => vi.advanceTimersByTimeAsync(600))
    expect(FakeSocket.instances).toHaveLength(3)
  })

  it('opens no socket after unmounting during the reconnect wait', async () => {
    vi.useFakeTimers()
    const { unmount } = renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(1006))
    unmount()
    await act(() => vi.advanceTimersByTimeAsync(5000))
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('gives up when the origin is refused', async () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(4403))
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(FakeSocket.instances).toHaveLength(1)
    expect(result.current.status).toBe('degraded')
  })

  it('refreshes the session before reconnecting when the token expired', async () => {
    let snapshots = 0
    server.use(http.get(SNAPSHOT, () => { snapshots++; return HttpResponse.json(priceState(5, '0.6000', '0.4000')) }))
    renderHook(() => useMarketPrices('m1'))
    // The snapshot fetched on open lands first, so only the refresh is counted after it.
    await waitFor(() => expect(snapshots).toBe(1))
    act(() => latest().serverClose(4408))
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 2000 })
    expect(snapshots).toBe(2)
  })

  it('stops when the session cannot be refreshed', async () => {
    server.use(
      http.get(SNAPSHOT, () =>
        HttpResponse.json({ error: { code: 'invalid_token', message: 'Not authenticated.' } }, { status: 401 }),
      ),
    )
    const { result } = renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(4401))
    await wait(1500)
    expect(FakeSocket.instances).toHaveLength(1)
    expect(result.current.status).toBe('degraded')
  })
})
