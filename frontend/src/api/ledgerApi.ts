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
  // The book's counter, incremented under the lock on every trade, with a
  // CHECK (state_version >= 0) on the column. **Zero means nobody has ever
  // traded this market**, and its prices are the untouched LMSR seed — a
  // uniform 50/50 on a binary book, which is an opening price and not an
  // opinion anybody holds (#445).
  state_version: number
  prices: OutcomePrice[]
  // The book's `state_changed_at`: when the trade that last moved `q` took
  // its price. On a market nobody has traded this is `opened_at` instead —
  // opening the book was the last state change there has been (D-029,
  // ledger-service.md) — so it is only a "last traded" time when
  // `state_version` is above zero.
  occurred_at: string
}

export async function getMarketSnapshot(marketId: string): Promise<PriceState> {
  const res = await api.get<PriceState>(`${LEDGER_BASE}/ledger/markets/${marketId}/snapshot`)
  return res.data
}

// One batched item: the single snapshot's body, field for field, plus how much
// the market has traded. Only the batch carries `credits_traded` — neither the
// single snapshot nor the `price` frame does (ledger-service.md) — so it is
// optional on the type a card renders from all three.
export interface MarketSnapshot extends PriceState {
  /**
   * Each outcome's price seven days before the request, in the shape and
   * order of `prices` ([X-1] #446). A card's change over the week is `price`
   * minus the entry here with the same `outcome_id`.
   *
   * **Null when there is no change to measure**, which is three cases the card
   * treats alike: the market has no trade that old (never traded, or only
   * within the week — a book standing at its seed price is not a price anybody
   * chose), or the ledger could not rebuild it. Equal to `prices` is different
   * again: a real change of zero, not missing data.
   */
  prices_7d_ago: OutcomePrice[] | null
  credits_traded: string
}

// The batch route's own limit (`MAX_SNAPSHOT_IDS` in the ledger's schemas.py).
const MAX_SNAPSHOT_IDS = 200

/**
 * The current prices of many markets in one request ([X-1] #432).
 *
 * At most 200 ids go in one request — market_service's largest page, so one
 * page of the browse list always fits. A 201st is a 422 naming `market_ids`
 * rather than a short answer, so a longer list is sent here as several
 * requests and answered as one: a browse list grows past 200 by Load more.
 *
 * A market is absent from the answer rather than erroring when this service
 * holds no book for it — an unknown id, a draft, and a published market
 * nobody has previewed or traded all read the same way. **This route opens no
 * book**, so a cold published market stays absent however often it is asked
 * for; `getMarketSnapshot` is what opens one, and from then on the batch
 * answers it. An empty list is a request worth not sending.
 */
export async function getMarketSnapshots(marketIds: string[]): Promise<MarketSnapshot[]> {
  const requests: Promise<MarketSnapshot[]>[] = []
  for (let start = 0; start < marketIds.length; start += MAX_SNAPSHOT_IDS) {
    const chunk = marketIds.slice(start, start + MAX_SNAPSHOT_IDS)
    requests.push(
      api.post<{ snapshots: MarketSnapshot[] }>(`${LEDGER_BASE}/ledger/markets/snapshots`, { market_ids: chunk })
        .then(res => res.data.snapshots),
    )
  }
  const answers = await Promise.all(requests)
  return answers.flat()
}

export interface Balance {
  user_id: string
  account_id: string
  // A decimal string, not a JSON number (ledger-service.md). Format with
  // formatCredits; never parseFloat it.
  //
  // `balance` is the sum of every entry, resting buy orders' reserved
  // credits included. `reserved` is what those orders could still spend and
  // `available` is `balance - reserved` — what a new trade or buy order may
  // actually spend (ADR 0024). Spend checks read `available`, never
  // `balance`; net worth still reads `balance`.
  balance: string
  reserved: string
  available: string
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
  /**
   * The part of `quantity` the trader's own live sell orders of this outcome
   * hold back, so a sell — instant or a new order — may take
   * `quantity - reserved_shares` and no more (#410).
   *
   * Never null, unlike the same name on an `Order`. The ledger works it out
   * from the order rows in the same query that reads `quantity`
   * (ledger_service/service/portfolio.py), so both describe the same moment.
   * `"0.0000"` where no live sell order holds any, and on a settled row,
   * since no order on a market with a result is live.
   */
  reserved_shares: string
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
  // Null on every row until the ledger settles its market. `result` is
  // `paid_out` for the winning outcome and `worthless` for any other;
  // `payout` is the credits this row was paid — the row's quantity on a
  // `paid_out` row, "0.0000" on a `worthless` one. Already in `balance`, so
  // it is not added into `positions_value` again (ledger-service.md).
  result: 'paid_out' | 'worthless' | null
  payout: string | null
  state_version: number
}

export interface Portfolio {
  user_id: string
  account_id: string
  balance: string
  positions_value: string
  // balance + positions_value, exactly — nothing rounded after the valuation.
  net_worth: string
  // What this user's signup grant actually paid them, read from that
  // transaction's own entry — not the STARTING_CREDITS setting, which can
  // change without rewriting an entry (ADR 0009).
  starting_credits: string
  // P&L already banked in `balance`: what the sells and settlements paid,
  // less the cost basis they released. Can be negative.
  realized_pnl: string
  // The sum of the positions' own `unrealized_pnl` — what is still riding on
  // positions nobody has closed. A settled row contributes nothing.
  unrealized_pnl: string
  // realized_pnl + unrealized_pnl, and net_worth - starting_credits, exactly.
  // The endpoint does the sums because the browser must not do arithmetic on
  // these strings ([B-1] #359): render this field, never a total of the
  // other two.
  total_pnl: string
  positions: Position[]
}

/**
 * The signed-in user's positions, cash, net worth and profit and loss
 * ([T-4] #24, [B-1] #359). Only positions with quantity > 0 are included — a
 * position sold to zero is omitted, not zeroed. Carries ids only; market
 * question and outcome labels are this frontend's own join, never
 * market_service's job here.
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

export interface LeaderboardRow {
  rank: number
  username: string
  net_worth: string
  is_me: boolean
}

/** The caller's own rank, username and net worth in the ranking a page came
 * from — the same three fields as a row, but not necessarily on this page
 * (ledger-service.md). Null when the caller is not in that ranking. */
export interface LeaderboardMe {
  rank: number
  username: string
  net_worth: string
}

export interface LeaderboardPage {
  // Null before the ledger has ever completed a refresh.
  computed_at: string | null
  // True once computed_at is more than two refresh intervals old — the board
  // shown is still the last complete ranking, never a partial one.
  stale: boolean
  // Every user the whole ranking holds, not just this page; the same on
  // every page of one ranking.
  ranked_users: number
  me: LeaderboardMe | null
  rows: LeaderboardRow[]
  next_cursor: string | null
  has_more: boolean
}

/** One page of the most recently computed ranking ([L-1] #38). `cursor`
 * holds the caller to the ranking the first page came from; after that
 * ranking has been replaced twice, the ledger answers `409
 * leaderboard_refreshed` and the caller starts again from page one
 * (ledger-service.md). */
export async function getLeaderboard(params?: { cursor?: string; limit?: number }): Promise<LeaderboardPage> {
  const res = await api.get<LeaderboardPage>(`${LEDGER_BASE}/ledger/leaderboard`, { params })
  return res.data
}

// A limit order's state, the same shape on all three order routes
// (ledger-service.md, "Limit orders"; ADR 0024).
export interface Order {
  id: string
  market_id: string
  outcome_id: string
  // 0 or 1. The ledger holds no labels — take them from market_service, the
  // way the portfolio does.
  outcome_position: number
  side: TradeSide
  // The price on this order's own outcome. A buy fills at or below it, a
  // sell at or above.
  limit_price: string
  quantity: string
  filled_quantity: string
  // What the fills averaged per share; null until the first fill.
  average_fill_price: string | null
  // A buy only: what the order can still spend. Null on a sell, "0.0000"
  // once the order is no longer live.
  reserved_credits: string | null
  // A sell only: the shares it still holds back. Null on a buy.
  reserved_shares: string | null
  // `released` means the market got a result and the remainder was freed; it
  // is derived on every read, not a stored value.
  status: 'open' | 'filled' | 'cancelled' | 'released'
  created_at: string
  cancelled_at: string | null
}

export interface OrderRequest {
  outcome_id: string
  side: TradeSide
  limit_price: string
  quantity: string
  idempotency_key: string
}

/** One fill a placement made on arrival: a trade in the caller's history. */
export interface OrderFill {
  transaction_id: string
  quantity: string
  // Signed the way the trade route signs it: negative on a buy.
  total: string
  state_version: number
}

export interface OrderResult {
  order: Order
  // What this request filled, empty when the order only rested. A retry with
  // the same key replays the first request's fills, not any a later trade's
  // cascade added (ledger-service.md).
  fills: OrderFill[]
  book_state_version: number
}

/**
 * Places a limit order ([T-6] #26). There is deliberately no `state_version`:
 * the limit price is the protection, and a quote check would refuse an order
 * for the very price move it was placed to catch (ADR 0024).
 *
 * Marketable on arrival means it fills at once, in part or whole, and
 * `fills` says what it filled; otherwise it rests. A buy's whole budget
 * (`limit_price × quantity`, rounded up to the tick) must fit in the
 * balance route's `available`, even when it would fill for less.
 *
 * `idempotency_key` must be regenerated only once the server has given a
 * definite answer, exactly as on `postTrade` — reusing it is what makes a
 * retry after a lost reply safe.
 */
export async function placeOrder(marketId: string, data: OrderRequest): Promise<OrderResult> {
  const res = await api.post<OrderResult>(`${LEDGER_BASE}/ledger/markets/${marketId}/orders`, data)
  return res.data
}

/**
 * Every live order of the signed-in trader, newest first ([T-6] #26): those
 * still `open` on a market with no result, closed markets included. Filled,
 * cancelled and released orders are not listed.
 *
 * Unpaged on purpose, so replace the whole list with each response rather
 * than merging into it (DECISIONS.md, "`GET /ledger/orders/me` returns every
 * live order, unpaged"). It is also the snapshot for order frames: read it
 * when the page opens and again after every socket reconnect.
 */
export async function getMyOrders(): Promise<{ orders: Order[] }> {
  const res = await api.get<{ orders: Order[] }>(`${LEDGER_BASE}/ledger/orders/me`)
  return res.data
}

export interface CancelOrderResult {
  // status "cancelled". `filled_quantity` is whatever filled first: a cancel
  // releases only the remainder, and fills already made stay.
  order: Order
  // Whichever the order reserved; the other is null.
  released_credits: string | null
  released_shares: string | null
}

/**
 * Cancels one of the caller's own live orders ([T-8] #28). No market gate: a
 * closed market's orders stay cancellable, and a suspended account can still
 * cancel while its token is valid.
 *
 * A cancel and a fill never both win — both take the market's book lock — so
 * the loser gets `order_already_filled`, and a retried cancel gets
 * `order_already_cancelled`, which means it is already done.
 */
export async function cancelOrder(orderId: string): Promise<CancelOrderResult> {
  const res = await api.post<CancelOrderResult>(`${LEDGER_BASE}/ledger/orders/${orderId}/cancel`)
  return res.data
}
