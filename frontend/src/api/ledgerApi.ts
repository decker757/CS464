import api from './axios'

// Same pattern as marketApi.ts: an absolute URL on the shared `api` instance,
// so the refresh interceptor and withCredentials come along.
const LEDGER_BASE = import.meta.env.VITE_LEDGER_API_URL ?? 'http://localhost:8003'

export interface OutcomePrice {
  outcome_id: string
  position: number
  price: string
}

// The snapshot and the websocket `price` frame are the same shape (minus the
// frame's `type`), so one type and one render path covers both.
// docs/api/realtime-service.md
export interface PriceState {
  market_id: string
  state_version: number
  prices: OutcomePrice[]
  occurred_at: string
}

export async function getMarketSnapshot(marketId: string): Promise<PriceState> {
  const res = await api.get<PriceState>(`${LEDGER_BASE}/ledger/markets/${marketId}/snapshot`)
  return res.data
}
