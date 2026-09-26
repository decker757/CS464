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

function priceState(version: number, yes: string, no: string) {
  return {
    market_id: 'm1',
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

describe('useMarketPrices', () => {
  beforeEach(() => {
    FakeSocket.instances = []
    // Stubbed per test: MSW installs its own WebSocket when the server starts.
    vi.stubGlobal('WebSocket', FakeSocket)
    server.use(http.get(SNAPSHOT, () => HttpResponse.json(priceState(5, '0.6000', '0.4000'))))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    configure({ reactStrictMode: false })
  })

  it('subscribes, then shows the snapshot in position order', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    expect(result.current).toBeNull()
    subscribe(latest())
    expect(latest().sent).toContainEqual({ action: 'subscribe', market_id: 'm1' })
    await waitFor(() => expect(result.current?.map(p => p.price)).toEqual(['0.6000', '0.4000']))
  })

  it('applies newer price frames and drops ones the snapshot already covers', async () => {
    const { result } = renderHook(() => useMarketPrices('m1'))
    subscribe(latest())
    await waitFor(() => expect(result.current?.[0].price).toBe('0.6000'))

    act(() => latest().receive({ type: 'price', ...priceState(4, '0.1000', '0.9000') }))
    expect(result.current?.[0].price).toBe('0.6000')

    act(() => latest().receive({ type: 'price', ...priceState(6, '0.7000', '0.3000') }))
    expect(result.current?.[0].price).toBe('0.7000')
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
    expect(result.current).toBeNull()
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

  it('gives up when the origin is refused', async () => {
    vi.useFakeTimers()
    renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(4403))
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('refreshes the session before reconnecting when the token expired', async () => {
    let snapshots = 0
    server.use(http.get(SNAPSHOT, () => { snapshots++; return HttpResponse.json(priceState(5, '0.6000', '0.4000')) }))
    renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(4408))
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(2), { timeout: 2000 })
    expect(snapshots).toBe(1)
  })

  it('stops when the session cannot be refreshed', async () => {
    server.use(
      http.get(SNAPSHOT, () =>
        HttpResponse.json({ error: { code: 'invalid_token', message: 'Not authenticated.' } }, { status: 401 }),
      ),
    )
    renderHook(() => useMarketPrices('m1'))
    act(() => latest().serverClose(4401))
    await wait(1500)
    expect(FakeSocket.instances).toHaveLength(1)
  })
})
