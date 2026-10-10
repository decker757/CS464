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

export interface AdminBalance {
  user_id: string
  // null for an id the ledger has never opened an account for — not an
  // error, and not grounds to mint one: this route mints nothing
  // (ledger-service.md, #188). balance reads "0.0000" in that case too.
  account_id: string | null
  balance: string
}

/**
 * An administrator's read of any user's balance ([4.1] #13), by the id
 * `GET /admin/users` on the auth service returns. Unlike `getMyBalance`,
 * never mints a starting grant — a user who has not read their own balance
 * or history yet still shows zero here.
 */
export async function getUserBalance(userId: string): Promise<AdminBalance> {
  const res = await api.get<AdminBalance>(`${LEDGER_BASE}/ledger/users/${userId}/balance`)
  return res.data
}

export interface Position {
  market_id: string
  outcome_id: string
  outcome_position: number
  quantity: string
  cost_basis: string
  average_entry_price: string
  // The outcome's marginal price, informational only — not the basis for `value`.
  price: string
  // What selling the whole position now would credit — a liquidation value,
  // never quantity × price (ledger-service.md, ADR 0018).
  value: string
  // value − cost_basis. Can be negative.
  unrealized_pnl: string
  state_version: number
}

export interface Portfolio {
  user_id: string
  account_id: string
  balance: string
  positions_value: string
  // balance + positions_value, exactly — nothing rounded after the valuation.
  net_worth: string
  positions: Position[]
}

/**
 * The signed-in user's positions, cash and net worth ([T-4] #24). Only
 * positions with quantity > 0 are included — a position sold to zero is
 * omitted, not zeroed. Carries ids only; market question and outcome labels
 * are this frontend's own join, never market_service's job here.
 */
export async function getMyPortfolio(): Promise<Portfolio> {
  const res = await api.get<Portfolio>(`${LEDGER_BASE}/ledger/portfolio/me`)
  return res.data
}

export type TradeSide = 'buy' | 'sell'

export interface LedgerEntry {
  id: string
  created_at: string
  // Signed for this account: negative took credits out, positive put them in
  // (ledger-service.md). Never parseFloat it; format with formatCreditsPrecise.
  amount: string
  balance_after: string
  transaction_id: string
  // The vocabulary of why credits moved. Treat a value this app does not
  // recognise as opaque, not an error — new ones arrive without a version
  // bump (ledger-service.md).
  kind: string
  context: Record<string, unknown>
  // Set only on trade_buy / trade_sell; null on every other kind, including
  // one this app does not recognise.
  market_id: string | null
  outcome_id: string | null
  side: TradeSide | null
  quantity: string | null
  // The average fill price for this trade, not the marginal price.
  average_price: string | null
}

interface LedgerEntryPage {
  entries: LedgerEntry[]
  next_cursor: string | null
  has_more: boolean
}

/**
 * One page of the signed-in user's own ledger history, newest first
 * ([T-5] #25) — the starting grant, every buy and sell, and anything else
 * that moved their credits. The first call for a user also mints their
 * starting grant, exactly as getMyBalance does.
 */
export async function getMyEntries(params?: { cursor?: string }): Promise<LedgerEntryPage> {
  const res = await api.get<LedgerEntryPage>(`${LEDGER_BASE}/ledger/entries/me`, { params })
  return res.data
}

/**
 * One page of any user's ledger history ([4.1] #13), by the id
 * `GET /admin/users` returns — same shape and order as `getMyEntries`, and
 * an id with no account reads as an empty page rather than an error. Never
 * mints a grant, unlike the `/me` route.
 */
export async function getUserEntries(userId: string, params?: { cursor?: string }): Promise<LedgerEntryPage> {
  const res = await api.get<LedgerEntryPage>(`${LEDGER_BASE}/ledger/users/${userId}/entries`, { params })
  return res.data
}

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

// --- [2.4] #8: price history and the per-market trade log ---------------
//
// #63 (the backend slice) has not been built yet, so these two shapes are
// inferred from #8's acceptance criteria rather than read off docs/api/ —
// the team's call, not a guess made to unblock this PR alone. Reconcile
// against the real contract once #63 lands.

/** One outcome's price at one point in time — the same shape the live
 * snapshot already uses, plus the moment it was true. */
export interface PricePoint extends OutcomePrice {
  occurred_at: string
}

export interface PriceHistory {
  market_id: string
  points: PricePoint[]
}

/** The price chart data for one market, every outcome, oldest first (#8's
 * "price chart per outcome over time"). Inferred: no real endpoint yet. */
export async function getMarketPriceHistory(marketId: string): Promise<PriceHistory> {
  const res = await api.get<PriceHistory>(`${LEDGER_BASE}/ledger/markets/${marketId}/price-history`)
  return res.data
}

export interface MarketTrade {
  id: string
  created_at: string
  username: string
  outcome_id: string
  side: TradeSide
  quantity: string
  average_price: string
}

interface MarketTradePage {
  trades: MarketTrade[]
  next_cursor: string | null
}

/** One page of every trade on one market, newest first, for #8's trade log —
 * unlike `getMyEntries`/`getUserEntries`, this crosses every trader, so each
 * row names its own `username`. Inferred: no real endpoint yet. */
export async function getMarketTrades(marketId: string, params?: { cursor?: string }): Promise<MarketTradePage> {
  const res = await api.get<MarketTradePage>(`${LEDGER_BASE}/ledger/markets/${marketId}/trades`, { params })
  return res.data
}

// --- [2.2] #55: market exposure --------------------------------------
//
// #6 (the backend slice) has not been built yet, so this shape is inferred
// from #6's own acceptance criteria rather than read off docs/api/, which
// has nothing for it yet — the team's call, not a guess made to unblock this
// PR alone. A winning share pays exactly 1 credit (docs/api/ledger-service.md's
// settlement section, "one credit per winning share"), so max_payout is the
// outcome's shares_outstanding read as credits. Reconcile against the real
// contract once #6 lands.

export interface OutcomeExposure {
  outcome_id: string
  position: number
  shares_outstanding: string
  // What this outcome would pay out if it resolved true: shares_outstanding,
  // one credit each.
  max_payout: string
}

export interface MarketExposure {
  market_id: string
  // Empty, with pool null and exceeds_pool false, on a market the ledger has
  // never opened a book for (#6's notes: "a 200 with nothing in it").
  outcomes: OutcomeExposure[]
  // seed_subsidy plus everything traders have paid in — not the seed alone
  // (#6's acceptance criteria). Null when there is no book yet.
  pool: string | null
  // True when any outcome's max_payout exceeds pool.
  exceeds_pool: boolean
}

/** Per-outcome shares outstanding and max payout for one market, plus
 * whether any outcome's payout would exceed the pool ([2.2] #6, admin panel
 * #55). Inferred: no real endpoint yet. */
export async function getMarketExposure(marketId: string): Promise<MarketExposure> {
  const res = await api.get<MarketExposure>(`${LEDGER_BASE}/ledger/markets/${marketId}/exposure`)
  return res.data
}
