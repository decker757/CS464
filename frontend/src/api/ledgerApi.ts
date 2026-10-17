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
  // These three are null on a position in a settled market: it is no longer
  // valued, so they are not zero either ([3.4] #12).
  price: string | null
  // What selling the whole position now would credit — a liquidation value,
  // never quantity × price (ledger-service.md, ADR 0018).
  value: string | null
  // value − cost_basis. Can be negative.
  unrealized_pnl: string | null
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

// --- [2.2] #55: market exposure --------------------------------------

export interface OutcomeExposure {
  outcome_id: string
  position: number
  shares_outstanding: string
  // What this outcome would pay out if it resolved true: null once the
  // market is settled (docs/api/ledger-service.md).
  payout_if_wins: string | null
  // pool - payout_if_wins. Negative is what the platform would have to top
  // up; null once the market is settled.
  residue_if_wins: string | null
}

export interface MarketExposure {
  market_id: string
  // null when the ledger has never opened a book for this market yet.
  opened_at: string | null
  state_version: number | null
  seed_subsidy: string | null
  // seed_subsidy plus everything traders have paid in, minus everything
  // sold. Null when there is no book yet, or once the market is settled.
  pool: string | null
  // True when any outcome's payout_if_wins is strictly greater than pool.
  // Always false once the market is settled.
  payout_exceeds_pool: boolean
  // null until the ledger records how the market ended.
  result: 'settled' | null
  winning_outcome_id: string | null
  outcomes: OutcomeExposure[]
}

/** Per-outcome shares outstanding and payout for one market, plus whether
 * any outcome's payout would exceed the pool ([2.2] #6, admin panel #55). */
export async function getMarketExposure(marketId: string): Promise<MarketExposure> {
  const res = await api.get<MarketExposure>(`${LEDGER_BASE}/ledger/markets/${marketId}/exposure`)
  return res.data
}

// --- [2.4] #8: price history and the per-market trade log ---------------
//
// Both shapes are Ihsan's for #63, sent ahead of docs/api/ landing there.

/** Every outcome's price at one point in time — basically a price socket
 * frame (same `state_version`/`occurred_at`/`prices` shape), so a live frame
 * can be appended and deduped on `state_version`. The first point is the
 * opening, uniform price. */
export interface PricePoint {
  state_version: number
  occurred_at: string
  prices: OutcomePrice[]
}

export interface PriceHistory {
  market_id: string
  // Oldest first. Empty, with every field below null, on a market the
  // ledger has never opened a book for.
  points: PricePoint[]
  result: 'settled' | null
  winning_outcome_id: string | null
  result_recorded_at: string | null
}

/** The price chart data for one market, every outcome, oldest first (#8's
 * "price chart per outcome over time"). */
export async function getMarketPriceHistory(marketId: string): Promise<PriceHistory> {
  const res = await api.get<PriceHistory>(`${LEDGER_BASE}/ledger/markets/${marketId}/price-history`)
  return res.data
}

export interface MarketTrade {
  transaction_id: string
  // `created_at`, not `occurred_at`: a trade is a ledger transaction row
  // (MarketTradeOut), while `occurred_at` belongs to a price point above.
  created_at: string
  // Not a username: the ledger holds no user table (ledger-service.md).
  // Shown as a short id until an admin batch lookup on auth exists.
  user_id: string
  outcome_id: string
  side: TradeSide
  quantity: string
  average_price: string
  total: string
  state_version: number
}

export interface MarketTradePage {
  trades: MarketTrade[]
  next_cursor: string | null
  has_more: boolean
}

/** One page of every trade on one market, newest first, admin-only, for #8's
 * trade log — unlike `getMyEntries`/`getUserEntries`, this crosses every
 * trader, so each row names its own `user_id` rather than reading "me". */
export async function getMarketTrades(marketId: string, params?: { cursor?: string; limit?: number }): Promise<MarketTradePage> {
  const res = await api.get<MarketTradePage>(`${LEDGER_BASE}/ledger/markets/${marketId}/trades`, { params })
  return res.data
}

export interface SettlementResult {
  market_id: string
  outcome_id: string
  recorded_at: string
  holders_paid: number
  total_paid: string
  residue: string
}

/** Settles an approved market once it is `settleable` ([3.4] #12): the
 * winner and the holders come from this service's own book, never the
 * request, which is why it takes no body. Safe to repeat — a retry after
 * `settlement_unconfirmed` replays the same answer rather than paying twice
 * (ledger-service.md). */
export async function settleMarket(marketId: string): Promise<SettlementResult> {
  const res = await api.post<SettlementResult>(`${LEDGER_BASE}/ledger/markets/${marketId}/settlement`)
  return res.data
}
