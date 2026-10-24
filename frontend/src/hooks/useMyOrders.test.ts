import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../test/server'
import { holdUntilReleased } from '../test/holdUntilReleased'
import { useMyOrders } from './useMyOrders'

const ORDERS = 'http://localhost:8003/ledger/orders/me'

// The live ledger's own shape, from a placement against a running stack.
function order(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ord-1',
    market_id: 'm1',
    outcome_id: 'o1',
    outcome_position: 0,
    side: 'buy',
    limit_price: '0.3000',
    quantity: '50.0000',
    filled_quantity: '0.0000',
    average_fill_price: null,
    reserved_credits: '15.0000',
    reserved_shares: null,
    status: 'open',
    created_at: '2026-10-20T15:52:10.744443Z',
    cancelled_at: null,
    ...overrides,
  }
}

function orderFrame(overrides: Record<string, unknown> = {}) {
  const { fill, ...rest } = overrides as { fill?: { state_version: number } }
  return {
    type: 'order' as const,
    order_id: 'ord-1',
    market_id: 'm1',
    status: 'open' as const,
    quantity: '50.0000',
    filled_quantity: '20.0000',
    fill: { state_version: 5, ...fill },
    ...rest,
  }
}

function mockOrders(orders: Record<string, unknown>[]) {
  server.use(http.get(ORDERS, () => HttpResponse.json({ orders })))
}

describe('useMyOrders', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('reads the list on mount', async () => {
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(1))
    expect(result.current.orders?.[0].limit_price).toBe('0.3000')
  })

  it('reads nothing at all while disabled', async () => {
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(false))
    // A market the form is hidden on costs the ledger nothing.
    await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(result.current.orders).toBeNull()
  })

  it('reads nothing on a refetch while disabled either', async () => {
    // The page re-reads on every socket ack, on markets with no limit orders
    // too; those reads must still cost the ledger nothing.
    let reads = 0
    server.use(http.get(ORDERS, () => { reads += 1; return HttpResponse.json({ orders: [order()] }) }))
    const { result } = renderHook(() => useMyOrders(false))

    act(() => { result.current.refetch() })
    await act(() => new Promise(resolve => setTimeout(resolve, 10)))

    expect(reads).toBe(0)
    expect(result.current.orders).toBeNull()
  })

  it('moves filled_quantity on the order a frame names', async () => {
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(1))

    act(() => result.current.applyFrame(orderFrame()))

    expect(result.current.orders?.[0].filled_quantity).toBe('20.0000')
  })

  it('discards a frame at or below the version already applied', async () => {
    // realtime-service.md: keep the highest fill version per order and drop
    // anything at or below it. Two fills of one order are in one market, so
    // their versions are ordered.
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(1))

    act(() => result.current.applyFrame(orderFrame({ filled_quantity: '20.0000', fill: { state_version: 5 } })))
    act(() => result.current.applyFrame(orderFrame({ filled_quantity: '9.0000', fill: { state_version: 4 } })))

    expect(result.current.orders?.[0].filled_quantity).toBe('20.0000')
  })

  it('drops an order from the list once a frame says it filled', async () => {
    // The list holds live orders only, which is what the next read would
    // show too (ledger-service.md).
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(1))

    act(() => result.current.applyFrame(orderFrame({ status: 'filled', filled_quantity: '50.0000' })))

    expect(result.current.orders).toHaveLength(0)
  })

  it('leaves an order a frame does not name alone', async () => {
    mockOrders([order(), order({ id: 'ord-2', limit_price: '0.4000' })])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(2))

    act(() => result.current.applyFrame(orderFrame({ order_id: 'ord-2', filled_quantity: '7.0000' })))

    expect(result.current.orders?.find(o => o.id === 'ord-1')?.filled_quantity).toBe('0.0000')
    expect(result.current.orders?.find(o => o.id === 'ord-2')?.filled_quantity).toBe('7.0000')
  })

  it('keeps a frame that arrives while a read is in flight, rather than letting the older snapshot undo it', async () => {
    // #393's review names this race: the read answers from before the frame
    // was published, so replacing the list with it would lose the fill.
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(1))

    const slowRead = holdUntilReleased()
    server.use(http.get(ORDERS, async () => {
      await slowRead.held
      // The snapshot the ledger had before the fill.
      return HttpResponse.json({ orders: [order({ filled_quantity: '0.0000' })] })
    }))

    act(() => { result.current.refetch() })
    act(() => result.current.applyFrame(orderFrame({ filled_quantity: '20.0000', fill: { state_version: 9 } })))
    await act(async () => { slowRead.release() })

    await waitFor(() => expect(result.current.orders?.[0].filled_quantity).toBe('20.0000'))
  })

  it('does not let a buffered frame take filled_quantity back below a newer read', async () => {
    // A frame can arrive while a read is in flight that the ledger answered
    // after a later fill. Re-applying it on top of that read would move the
    // figure backwards; filled_quantity only ever grows.
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(1))

    const slowRead = holdUntilReleased()
    server.use(http.get(ORDERS, async () => {
      await slowRead.held
      // Already past the fill the frame below reports, and one more.
      return HttpResponse.json({ orders: [order({ filled_quantity: '20.0000' })] })
    }))

    act(() => { result.current.refetch() })
    act(() => result.current.applyFrame(orderFrame({ filled_quantity: '12.0000', fill: { state_version: 5 } })))
    await act(async () => { slowRead.release() })

    await waitFor(() => expect(result.current.orders?.[0].filled_quantity).toBe('20.0000'))
    // Settled, not passing through: nothing is left to apply.
    await act(() => new Promise(resolve => setTimeout(resolve, 10)))
    expect(result.current.orders?.[0].filled_quantity).toBe('20.0000')
  })

  it('keeps the last list on screen when a re-read fails', async () => {
    mockOrders([order()])
    const { result } = renderHook(() => useMyOrders(true))
    await waitFor(() => expect(result.current.orders).toHaveLength(1))

    server.use(http.get(ORDERS, () => HttpResponse.error()))
    act(() => { result.current.refetch() })

    // A failed refetch after a cancel must not look like every order vanished.
    await waitFor(() => expect(result.current.hasError).toBe(true))
    expect(result.current.orders).toHaveLength(1)
  })

  it('reports an error when the very first read fails', async () => {
    server.use(http.get(ORDERS, () => HttpResponse.error()))
    const { result } = renderHook(() => useMyOrders(true))

    await waitFor(() => expect(result.current.hasError).toBe(true))
    expect(result.current.orders).toBeNull()
  })
})
