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

export interface Balance {
  user_id: string
  account_id: string
  // A decimal string, not a JSON number (ledger-service.md). Format with
  // formatCredits; never parseFloat it.
  balance: string
}

/**
 * The signed-in user's available balance ([B-2] #33). The first call for a
 * user also mints their starting credits (ledger-service.md) — there is no
 * separate grant step on the frontend's side.
 */
export async function getMyBalance(): Promise<Balance> {
  const res = await api.get<Balance>(`${LEDGER_BASE}/ledger/balances/me`)
  return res.data
}

export type TradeSide = 'buy' | 'sell'

export interface TradePreview {
  market_id: string
  state_version: number
  side: TradeSide
  outcome_id: string
  quantity: string
  // Negative on a buy (credits leave the trader), positive on a sell.
  total: string
  average_price: string
  prices: OutcomePrice[]
  post_trade_prices: OutcomePrice[]
}

/**
 * What a trade would cost right now, and how it would move every outcome's
 * price ([T-1] #21). Any authenticated account may call this — it mints
 * nothing and reveals nothing beyond the public market read. `state_version`
 * on the response is the quote to send back unchanged on the trade itself;
 * never substitute one read elsewhere, since a quote taken against stale
 * prices is the thing `state_version` exists to catch.
 */
export async function previewTrade(marketId: string, params: { outcomeId: string; side: TradeSide; quantity: string }): Promise<TradePreview> {
  const res = await api.get<TradePreview>(`${LEDGER_BASE}/ledger/markets/${marketId}/preview`, {
    params: { outcome_id: params.outcomeId, side: params.side, quantity: params.quantity },
  })
  return res.data
}

export interface TradeRequest {
  outcome_id: string
  side: TradeSide
  quantity: string
  state_version: number
  idempotency_key: string
}

export interface TradeResult {
  transaction_id: string
  user_id: string
  market_id: string
  outcome_id: string
  side: TradeSide
  quantity: string
  total: string
  state_version: number
}

/**
 * Buys or sells shares ([T-2] #22, [T-3] #23). `state_version` must be the
 * exact value the preview quoted for this trade — never refetched — so a
 * market that moved between the quote and the click is caught as
 * `409 quote_stale` rather than silently honoured.
 */
export async function postTrade(marketId: string, data: TradeRequest): Promise<TradeResult> {
  const res = await api.post<TradeResult>(`${LEDGER_BASE}/ledger/markets/${marketId}/trades`, data)
  return res.data
}
