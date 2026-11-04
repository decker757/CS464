// [5.4] #20. Load test of the trade endpoint: USERS traders (50 by default)
// each buy one share of an outcome and sell it back, once a second, on one
// market, for DURATION. Five stale quotes in a row and it gives up on that trade. Run it with loadtest/run.sh, which docs/load-test.md
// explains. The numbers that matter are http_req_duration{name:trade}'s p95,
// trade_errors and quote_stale_retries.
//
// The traders are the same accounts every run, loadtest_01 to loadtest_50:
// from one address the auth service registers 20 accounts an hour (ADR 0027),
// so a run cannot make 50 fresh ones. setup() registers any that do not exist,
// lifts the suspension the last run left, and logs each in. teardown()
// suspends them all again, which keeps them off the leaderboard between runs.
import http from 'k6/http'
import { check, fail, sleep } from 'k6'
import exec from 'k6/execution'
import { Counter, Rate } from 'k6/metrics'

const DOMAIN = __ENV.DOMAIN
const AUTH_URL = __ENV.AUTH_URL || `https://auth.${DOMAIN}`
const LEDGER_URL = __ENV.LEDGER_URL || `https://ledger.${DOMAIN}`
const MARKET_ID = __ENV.MARKET_ID
const USERS = Number(__ENV.USERS || 50)
// At most about 12 minutes: the traders' access tokens, minted in setup, last
// 15 (auth's access_token_ttl_seconds), and nothing here refreshes them.
const DURATION = __ENV.DURATION || '5m'
// Logins from one address are limited to 30 a minute (ADR 0027).
const LOGIN_SPACING_SECONDS = Number(__ENV.LOGIN_SPACING_SECONDS || 2.1)
// A trade quoted against a book another trader has just moved is refused
// 409 quote_stale with the current state_version, as a browser's would be.
// The trader retries at that version, like a person pressing Buy again.
const MAX_QUOTE_RETRIES = 5
const USERNAME_PREFIX = 'loadtest_'
const SUSPENSION_REASON = 'Load test account (#20), suspended between runs to keep it off the leaderboard.'
const UNSUSPENSION_REASON = 'Load test account (#20), unsuspended for a load test run.'

// open() works only here, in the init stage. The two passwords are files on
// the machine running the test, mounted by run.sh, and never arguments.
const ADMIN_IDENTIFIER = __ENV.ADMIN_IDENTIFIER
const ADMIN_PASSWORD = open('/secrets/admin-password').trim()
const TRADER_PASSWORD = open('/secrets/trader-password').trim()

const tradeErrors = new Rate('trade_errors')
const quoteStaleRetries = new Counter('quote_stale_retries')
const tradesCommitted = new Counter('trades_committed')
// Five stale quotes in a row: contention on one book, not a server error.
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

function login(identifier, password) {
  return http.post(`${AUTH_URL}/auth/login`, JSON.stringify({ identifier, password }),
    { ...jsonHeaders(), tags: { name: 'login' } })
}

function register(username) {
  const body = { username, email: `${username}@example.com`, password: TRADER_PASSWORD }
  return http.post(`${AUTH_URL}/auth/register`, JSON.stringify(body),
    { ...jsonHeaders(), tags: { name: 'register' } })
}

function setSuspension(adminToken, userId, suspended) {
  const reason = suspended ? SUSPENSION_REASON : UNSUSPENSION_REASON
  const response = http.patch(`${AUTH_URL}/admin/users/${userId}/suspension`,
    JSON.stringify({ suspended, reason }), { ...jsonHeaders(adminToken), tags: { name: 'suspension' } })
  if (response.status !== 200) {
    fail(`suspension of ${userId} answered ${response.status}: ${response.body}`)
  }
}

function listLoadTestUsers(adminToken) {
  const response = http.get(`${AUTH_URL}/admin/users?q=${USERNAME_PREFIX}&limit=200`,
    { ...jsonHeaders(adminToken), tags: { name: 'admin_users' } })
  if (response.status !== 200) {
    fail(`GET /admin/users answered ${response.status}: ${response.body}`)
  }
  return response.json('users').filter((user) => user.username.startsWith(USERNAME_PREFIX))
}

function snapshotOf(token) {
  return http.get(`${LEDGER_URL}/ledger/markets/${MARKET_ID}/snapshot`,
    { ...jsonHeaders(token), tags: { name: 'snapshot' } })
}

function loginTrader(username) {
  let response = login(username, TRADER_PASSWORD)
  if (response.status === 401) {
    response = register(username)
  }
  if (response.status !== 200 && response.status !== 201) {
    fail(`${username} could not log in or register: ${response.status} ${response.body}`)
  }
  return { username, token: response.json('tokens.access_token'), id: response.json('user.id') }
}

/** One trade, retried at the current state_version while it is quote_stale. */
function trade({ token, runId, outcomeId, side, stateVersion }) {
  let version = stateVersion
  for (let attempt = 0; attempt <= MAX_QUOTE_RETRIES; attempt++) {
    const body = {
      outcome_id: outcomeId,
      side,
      quantity: '1',
      state_version: version,
      // Unique across runs too: a key reused with the same body would replay
      // the earlier run's trade instead of making a new one.
      idempotency_key: `${runId}-${exec.vu.idInTest}-${exec.vu.iterationInScenario}-${side}-${attempt}`,
    }
    const response = http.post(`${LEDGER_URL}/ledger/markets/${MARKET_ID}/trades`, JSON.stringify(body), {
      ...jsonHeaders(token),
      tags: { name: 'trade' },
      // A 409 is the book doing its job, not a failed request.
      responseCallback: http.expectedStatuses(201, 409),
    })
    if (response.status === 201) {
      tradeErrors.add(false)
      tradesCommitted.add(1)
      return response.json('state_version')
    }
    if (response.status === 409 && response.json('error.code') === 'quote_stale') {
      quoteStaleRetries.add(1)
      version = response.json('error.details.current')
      continue
    }
    tradeErrors.add(true)
    console.warn(`${side} answered ${response.status}: ${response.body}`)
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

export function setup() {
  if (!DOMAIN && !(__ENV.AUTH_URL && __ENV.LEDGER_URL)) {
    fail('DOMAIN is required, or both AUTH_URL and LEDGER_URL')
  }
  if (!MARKET_ID || !ADMIN_IDENTIFIER) {
    fail('MARKET_ID and ADMIN_IDENTIFIER are required')
  }
  const adminToken = loginAdmin()

  const snapshot = snapshotOf(adminToken)
  if (snapshot.status !== 200) {
    fail(`the market's snapshot answered ${snapshot.status}: ${snapshot.body}`)
  }
  const outcomeIds = snapshot.json('prices').map((price) => price.outcome_id)

  for (const user of listLoadTestUsers(adminToken)) {
    if (user.is_suspended) {
      setSuspension(adminToken, user.id, false)
    }
  }
  const traders = []
  for (let index = 0; index < USERS; index++) {
    traders.push(loginTrader(usernameOf(index)))
    sleep(LOGIN_SPACING_SECONDS)
  }
  return { runId: Date.now(), outcomeIds, traders }
}

export default function (data) {
  const trader = data.traders[exec.vu.idInTest - 1]
  const outcomeId = data.outcomeIds[exec.vu.idInTest % data.outcomeIds.length]
  const snapshot = snapshotOf(trader.token)
  check(snapshot, { 'snapshot is 200': (response) => response.status === 200 })
  if (snapshot.status === 200) {
    const order = { token: trader.token, runId: data.runId, outcomeId }
    const afterBuy = trade({ ...order, side: 'buy', stateVersion: snapshot.json('state_version') })
    if (afterBuy !== null) {
      trade({ ...order, side: 'sell', stateVersion: afterBuy })
    }
  }
  sleep(1)
}

// Logs the admin in again: setup's token may have expired (15 minutes) by now.
export function teardown(data) {
  const adminToken = loginAdmin()
  for (const trader of data.traders) {
    setSuspension(adminToken, trader.id, true)
  }
}
