import { StrictMode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

    await waitFor(() => expect(result.current.pricesByMarket.get('m1')).toEqual([{ outcome_id: 'o1', position: 0, price: '0.6000' }]))
  })

  // [X-1] #364 AC 1: a list left open showed the prices it loaded with, for
  // as long as it stayed open, and said nothing about it.
  it('reads the prices again on a timer, without a reload', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    serveChangingPrices(['0.6000', '0.7000'])

    const { result } = renderHook(() => usePricesForMarkets(TRADING))
    const priceNow = () => result.current.pricesByMarket.get('m1')?.[0].price
    await waitFor(() => expect(priceNow()).toBe('0.6000'))
    const firstReadAt = result.current.readAt?.getTime() ?? 0

    await act(async () => { vi.advanceTimersByTime(PRICE_REFRESH_MS) })

    await waitFor(() => expect(priceNow()).toBe('0.7000'))
    // The screen reports when the prices were read, so the refresh has to move it.
    expect(result.current.readAt?.getTime()).toBeGreaterThan(firstReadAt)
  })

  // A market that can no longer trade can no longer get a new price, and each
  // read costs the ledger an LMSR evaluation.
  it('stops reading a market whose close time has passed, whatever its status says', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const reads = serveChangingPrices(['0.6000'])

    const { result } = renderHook(() => usePricesForMarkets(PAST_ITS_CLOSE))
    await waitFor(() => expect(result.current.pricesByMarket.get('m1')).toHaveLength(1))

    await act(async () => { vi.advanceTimersByTime(3 * PRICE_REFRESH_MS) })

    expect(reads.count).toBe(1)
  })
})
