import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { holdUntilReleased } from '../test/holdUntilReleased'
import { server } from '../test/server'
import { PRICE_REFRESH_MS, usePricesForMarkets } from './usePricesForMarkets'

const SNAPSHOT = 'http://localhost:8003/ledger/markets/:id/snapshot'

// One array for every render: a new array each time would be a new list.
const TRADING = [{ id: 'm1', status: 'open' as const, close_time: '2099-01-05T12:00:00Z' }]
// Its status column still reads open, and the clock says it closed an hour
// ago. ADR 0011: the clock is the authority.
const PAST_ITS_CLOSE = [{ id: 'm1', status: 'open' as const, close_time: '2020-01-01T00:00:00Z' }]

function snapshotPriced(price: string) {
  return {
    market_id: 'm1',
    state_version: 1,
    prices: [{ outcome_id: 'o1', position: 0, price }],
    occurred_at: '2026-10-18T00:00:00Z',
  }
}

// Answers each snapshot request with the next price, and counts the requests.
function serveChangingPrices(prices: string[]) {
  const reads = { count: 0 }
  server.use(
    http.get(SNAPSHOT, () => {
      const price = prices[Math.min(reads.count, prices.length - 1)]
      reads.count += 1
      return HttpResponse.json(snapshotPriced(price))
    }),
  )
  return reads
}

describe('usePricesForMarkets', () => {
  afterEach(() => vi.useRealTimers())

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

  // [X-1] #364 AC 1: a list left open showed the prices it loaded with, for
  // as long as it stayed open, and said nothing about it.
  it('reads the prices again on a timer, without a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serveChangingPrices(['0.6000', '0.7000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    const priceNow = () => result.current.snapshotsByMarket.get('m1')?.prices[0].price
    await waitFor(() => expect(priceNow()).toBe('0.6000'))
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
    const lastGoodRead = result.current.readAt?.getTime()

    let failedReads = 0
    server.use(http.get(SNAPSHOT, () => {
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
  // queue a second read per card, and a slow ledger — several books opening
  // for the first time — is exactly when that happens. Same reason
  // LoadMoreControl disables its button while a page is on its way.
  it('does not ask again about a market it is still waiting on', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { held, release } = holdUntilReleased()
    let reads = 0
    server.use(http.get(SNAPSHOT, async () => {
      reads += 1
      await held
      return HttpResponse.json(snapshotPriced('0.6000'))
    }))

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    await waitFor(() => expect(reads).toBe(1))

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
    server.use(http.get(SNAPSHOT, () =>
      failing ? HttpResponse.error() : HttpResponse.json(snapshotPriced('0.6000')),
    ))

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
    let reads = 0
    server.use(http.get(SNAPSHOT, () => {
      reads += 1
      return HttpResponse.error()
    }))

    const { result } = renderHook(() => usePricesForMarkets(PAST_ITS_CLOSE))
    await waitFor(() => expect(result.current.failedMarkets.has('m1')).toBe(true))

    act(() => result.current.retryMarket('m1'))

    await waitFor(() => expect(reads).toBe(2))
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
