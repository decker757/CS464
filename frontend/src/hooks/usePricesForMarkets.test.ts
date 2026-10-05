import { StrictMode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { usePricesForMarkets } from './usePricesForMarkets'

const SNAPSHOT = 'http://localhost:8003/ledger/markets/:id/snapshot'

// One array for every render: a new array each time would be a new list.
const MARKET_IDS = ['m1']

describe('usePricesForMarkets', () => {
  // #104 amendment D: the hook skips markets it already has prices for, and
  // StrictMode (on in main.tsx) mounts it, cancels that first request and
  // mounts it again. A market counted as fetched when its request went out,
  // rather than when its answer landed, would never be asked for again and
  // its card would show "—" for good.
  it('still prices every market when StrictMode cancels the first mount', async () => {
    server.use(
      http.get(SNAPSHOT, () => HttpResponse.json({
        market_id: 'm1',
        state_version: 1,
        prices: [{ outcome_id: 'o1', position: 0, price: '0.6000' }],
        occurred_at: '2026-10-05T00:00:00Z',
      })),
    )

    const { result } = renderHook(() => usePricesForMarkets(MARKET_IDS), { wrapper: StrictMode })

    await waitFor(() => expect(result.current.get('m1')).toEqual([{ outcome_id: 'o1', position: 0, price: '0.6000' }]))
  })
})
