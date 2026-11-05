// [5.4] #20. Load test of the trade endpoint: USERS traders (50 by default)
// each buy one share of an outcome and sell it back, once a second, on one
// market, for DURATION. A trade still refused at its price limit after five
// retries is given up.
// MARKET_IDS names one market, or several separated by commas: the traders
// are dealt across them in turn, so 50 traders on 5 markets is 10 a market.
// Run it with loadtest/run.sh; docs/load-test.md explains it.
//
// The traders are the same accounts every run, loadtest_01 to loadtest_50:
// one address may register only 20 accounts an hour (ADR 0027), so a run
// cannot make 50 fresh ones. setup() registers any that do not exist, lifts
// the suspension the last run left, and logs each in. teardown() suspends
// them again, which keeps them off the leaderboard between runs.
import http from 'k6/http'
import { check, fail, sleep } from 'k6'
import exec from 'k6/execution'
import { Counter, Rate } from 'k6/metrics'

const DOMAIN = __ENV.DOMAIN
const AUTH_URL = __ENV.AUTH_URL || `https://auth.${DOMAIN}`
const MARKET_URL = __ENV.MARKET_URL || `https://market.${DOMAIN}`
const LEDGER_URL = __ENV.LEDGER_URL || `https://ledger.${DOMAIN}`
const MARKET_IDS = (__ENV.MARKET_IDS || '').split(',').map((id) => id.trim()).filter((id) => id)
const USERS = Number(__ENV.USERS || 50)
// At most about 12 minutes: the traders' access tokens, minted in setup, last
// 15 (auth's access_token_ttl_seconds), and nothing here refreshes them.
const DURATION = __ENV.DURATION || '5m'
// Logins from one address are limited to 30 a minute (ADR 0027). 2.5 s apart
// is 24 a minute, which leaves room for the admin's and anyone else's.
const LOGIN_SPACING_SECONDS = Number(__ENV.LOGIN_SPACING_SECONDS || 2.5)
// A trade whose price another trader has moved past its limit is refused
// 409 price_beyond_limit, as a browser's would be. The trader reads a fresh
// snapshot and retries at that price, like a person pressing Buy again.
const MAX_LIMIT_RETRIES = 5
const QUANTITY = '1'
const LIMIT_SLACK_PERCENT = 5
const TICKS_PER_CREDIT = 10000
// The test trades only a market whose question starts with this, so a wrong
// market id cannot move a real market's price.
const THROWAWAY_MARKET_PREFIX = 'Load test'
const USERNAME_PREFIX = 'loadtest_'
const SUSPENSION_REASON = 'Load test account (#20), suspended between runs to keep it off the leaderboard.'
const UNSUSPENSION_REASON = 'Load test account (#20), unsuspended for a load test run.'

// open() works only here, in the init stage. The two passwords are files on
// the machine running the test, mounted by run.sh, and never arguments.
const ADMIN_IDENTIFIER = __ENV.ADMIN_IDENTIFIER
const ADMIN_PASSWORD = open('/secrets/admin-password').trim()
const TRADER_PASSWORD = open('/secrets/trader-password').trim()

// Anything but a commit or a refused price: the server's fault, or ours.
const tradeErrors = new Rate('trade_errors')
const priceLimitRefusals = new Counter('price_limit_refusals')
// Every trade here names a limit, so only a body without one is answered
// quote_stale (docs/api/ledger-service.md, "Rolling out"). Above 0 is a bug.
const quoteStaleRetries = new Counter('quote_stale_retries')
const tradesCommitted = new Counter('trades_committed')
// Still refused after five retries: contention on one book, not an error.
const tradesGivenUp = new Counter('trades_given_up')

export const options = {
  setupTimeout: '10m',
  teardownTimeout: '5m',
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    traders: { executor: 'constant-vus', vus: USERS, duration: DURATION },
  },
  thresholds: {
    // The same line as the p95 latency alert (deploy/grafana/alerts.yaml).
    'http_req_duration{name:trade}': ['p(95)<1000'],
    // k6 reports a tagged latency only when it has a threshold.
    'http_req_duration{name:snapshot}': ['p(95)<1000'],
    trade_errors: ['rate<0.01'],
  },
}

function usernameOf(index) {
  return `${USERNAME_PREFIX}${String(index + 1).padStart(2, '0')}`
}

function jsonHeaders(token) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return { headers }
}

/** The error code of a refusal. Never the whole body: a 422 echoes what was sent. */
function errorCodeOf(response) {
  try {
    return response.json('error.code') || 'no error code'
  } catch {
    return 'no JSON body'
  }
}

function login(identifier, password) {
  return http.post(`${AUTH_URL}/auth/login`, JSON.stringify({ identifier, password }),
    { ...jsonHeaders(), tags: { name: 'login' } })
}

function register(username) {
  const body = { username, email: `${username}@example.com`, password: TRADER_PASSWORD }
  return http.post(`${AUTH_URL}/auth/register`, JSON.stringify(body),
    { ...jsonHeaders(), tags: { name: 'register' } })
}

/** True if the account now has the suspension asked for. Logs a refusal and carries on. */
function setSuspension(adminToken, userId, suspended) {
  const reason = suspended ? SUSPENSION_REASON : UNSUSPENSION_REASON
  const response = http.patch(`${AUTH_URL}/admin/users/${userId}/suspension`,
    JSON.stringify({ suspended, reason }), { ...jsonHeaders(adminToken), tags: { name: 'suspension' } })
  if (response.status !== 200) {
    console.error(`setting suspended=${suspended} on ${userId} answered ${response.status} ${errorCodeOf(response)}`)
    return false
  }
  return true
}

function suspendAll(adminToken, userIds) {
  for (const userId of userIds) {
    setSuspension(adminToken, userId, true)
  }
}

/** The load test's own accounts that exist already, by username. */
function existingAccountsOf(adminToken) {
  const response = http.get(`${AUTH_URL}/admin/users?q=${USERNAME_PREFIX}&limit=200`,
    { ...jsonHeaders(adminToken), tags: { name: 'admin_users' } })
  if (response.status !== 200) {
    fail(`GET /admin/users answered ${response.status} ${errorCodeOf(response)}`)
  }
  const accounts = new Map()
  for (const user of response.json('users')) {
    accounts.set(user.username, user)
  }
  return accounts
}

// GET /markets/{id} answers only the market's creator, so ADMIN_IDENTIFIER
// must be the admin who created the throwaway market.
function refuseUnlessThrowawayMarket(adminToken, marketId) {
  const response = http.get(`${MARKET_URL}/markets/${marketId}`,
    { ...jsonHeaders(adminToken), tags: { name: 'market' } })
  if (response.status !== 200) {
    fail(`GET /markets/${marketId} answered ${response.status} ${errorCodeOf(response)}`)
  }
  const question = response.json('question')
  if (!question.startsWith(THROWAWAY_MARKET_PREFIX)) {
    fail(`market ${marketId} is "${question}"; the test trades only a market whose question starts "${THROWAWAY_MARKET_PREFIX}"`)
  }
}

function snapshotOf(token, marketId) {
  return http.get(`${LEDGER_URL}/ledger/markets/${marketId}/snapshot`,
    { ...jsonHeaders(token), tags: { name: 'snapshot' } })
}

/** The outcome's price and the book's version, from a 200 snapshot. */
function quoteOf(snapshot, outcomeId) {
  const price = snapshot.json('prices').find((entry) => entry.outcome_id === outcomeId).price
  return { price, stateVersion: snapshot.json('state_version') }
}

// The traders accept 5% off the snapshot's price, so the test measures the
// server, not the traders' patience. The script's one float sum: the price is
// read as whole ticks (0.0001), the limit rounded outward to a whole tick,
// and sent at four places, as money on the wire always is.
function limitFor(side, price) {
  const priceTicks = Math.round(Number(price) * TICKS_PER_CREDIT) * Number(QUANTITY)
  let limitTicks
  if (side === 'buy') {
    limitTicks = Math.ceil(priceTicks * (100 + LIMIT_SLACK_PERCENT) / 100)
  } else {
    limitTicks = Math.floor(priceTicks * (100 - LIMIT_SLACK_PERCENT) / 100)
  }
  return (limitTicks / TICKS_PER_CREDIT).toFixed(4)
}

function loginTrader(username) {
  let response = login(username, TRADER_PASSWORD)
  if (response.status === 429) {
    fail(`${username}: login refused 429 too_many_attempts. Raise LOGIN_SPACING_SECONDS, or wait a minute`)
  }
  if (response.status === 401) {
    response = register(username)
    if (response.status === 429) {
      fail(`${username}: registration refused 429 too_many_attempts. Registering 50 accounts from one address takes three hours: docs/load-test.md, "The first run"`)
    }
  }
  if (response.status !== 200 && response.status !== 201) {
    fail(`${username} could not log in or register: ${response.status} ${errorCodeOf(response)}`)
  }
  return { username, token: response.json('tokens.access_token'), id: response.json('user.id') }
}

/** One trade, retried at a fresh snapshot's price while its limit is refused. */
function trade({ token, runId, marketId, outcomeId, side, quote }) {
  const limitField = side === 'buy' ? 'max_cost' : 'min_proceeds'
  let current = quote
  for (let attempt = 0; attempt <= MAX_LIMIT_RETRIES; attempt++) {
    const body = {
      outcome_id: outcomeId,
      side,
      quantity: QUANTITY,
      [limitField]: limitFor(side, current.price),
      state_version: current.stateVersion,
      // Unique across runs too: a key reused with the same body would replay
      // the earlier run's trade instead of making a new one.
      idempotency_key: `${runId}-${exec.vu.idInTest}-${exec.vu.iterationInScenario}-${side}-${attempt}`,
    }
    const response = http.post(`${LEDGER_URL}/ledger/markets/${marketId}/trades`, JSON.stringify(body), {
      ...jsonHeaders(token),
      tags: { name: 'trade' },
      // A 409 is the book doing its job, not a failed request.
      responseCallback: http.expectedStatuses(201, 409),
    })
    if (response.status === 201) {
      tradeErrors.add(false)
      tradesCommitted.add(1)
      // Of the response's two counters, book_state_version is the one after everything the trade did.
      return response.json('book_state_version')
    }
    const code = errorCodeOf(response)
    if (response.status === 409 && (code === 'price_beyond_limit' || code === 'quote_stale')) {
      if (code === 'price_beyond_limit') {
        priceLimitRefusals.add(1)
      } else {
        quoteStaleRetries.add(1)
      }
      const snapshot = snapshotOf(token, marketId)
      if (snapshot.status !== 200) {
        tradeErrors.add(true)
        console.warn(`the snapshot before a ${side} retry answered ${snapshot.status} ${errorCodeOf(snapshot)}`)
        return null
      }
      current = quoteOf(snapshot, outcomeId)
      continue
    }
    tradeErrors.add(true)
    console.warn(`${side} answered ${response.status} ${code}`)
    return null
  }
  tradesGivenUp.add(1)
  return null
}

function loginAdmin() {
  const response = login(ADMIN_IDENTIFIER, ADMIN_PASSWORD)
  if (response.status !== 200 || response.json('user.role') !== 'admin') {
    fail(`the admin login answered ${response.status}, or the account is not an admin`)
  }
  return response.json('tokens.access_token')
}

/**
 * Logs in the USERS traders, registering or unsuspending each as needed.
 * k6 skips teardown when setup fails, so a failure here suspends every
 * account this run made or unsuspended before it stops the run.
 */
function prepareTraders(adminToken) {
  const accounts = existingAccountsOf(adminToken)
  const traders = []
  const unsuspended = []
  try {
    for (let index = 0; index < USERS; index++) {
      const username = usernameOf(index)
      const account = accounts.get(username)
      if (account && account.is_suspended) {
        if (!setSuspension(adminToken, account.id, false)) {
          fail(`${username} could not be unsuspended`)
        }
        unsuspended.push(account.id)
      }
      const trader = loginTrader(username)
      if (!account) {
        unsuspended.push(trader.id)
      }
      traders.push(trader)
      sleep(LOGIN_SPACING_SECONDS)
    }
  } catch (error) {
    suspendAll(adminToken, unsuspended)
    throw error
  }
  return traders
}

export function setup() {
  if (!DOMAIN && !(__ENV.AUTH_URL && __ENV.MARKET_URL && __ENV.LEDGER_URL)) {
    fail('DOMAIN is required, or all of AUTH_URL, MARKET_URL and LEDGER_URL')
  }
  if (MARKET_IDS.length === 0 || !ADMIN_IDENTIFIER) {
    fail('MARKET_IDS and ADMIN_IDENTIFIER are required')
  }
  const adminToken = loginAdmin()
  const markets = []
  for (const marketId of MARKET_IDS) {
    refuseUnlessThrowawayMarket(adminToken, marketId)
    const snapshot = snapshotOf(adminToken, marketId)
    if (snapshot.status !== 200) {
      fail(`market ${marketId}'s snapshot answered ${snapshot.status} ${errorCodeOf(snapshot)}`)
    }
    markets.push({ marketId, outcomeIds: snapshot.json('prices').map((price) => price.outcome_id) })
  }

  return { runId: Date.now(), markets, traders: prepareTraders(adminToken) }
}

export default function (data) {
  const index = exec.vu.idInTest - 1
  const trader = data.traders[index]
  const market = data.markets[index % data.markets.length]
  const outcomeId = market.outcomeIds[index % market.outcomeIds.length]
  const snapshot = snapshotOf(trader.token, market.marketId)
  check(snapshot, { 'snapshot is 200': (response) => response.status === 200 })
  if (snapshot.status === 200) {
    const order = { token: trader.token, runId: data.runId, marketId: market.marketId, outcomeId }
    const quote = quoteOf(snapshot, outcomeId)
    const afterBuy = trade({ ...order, side: 'buy', quote })
    if (afterBuy !== null) {
      // The buy has just raised this outcome's price, so the price it was
      // quoted at is still a fair floor for selling the share back.
      trade({ ...order, side: 'sell', quote: { price: quote.price, stateVersion: afterBuy } })
    }
  }
  sleep(1)
}

// Logs the admin in again: setup's token may have expired (15 minutes) by now.
export function teardown(data) {
  suspendAll(loginAdmin(), data.traders.map((trader) => trader.id))
}
