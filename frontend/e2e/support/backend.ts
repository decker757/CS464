// Test data, made through the backend's own HTTP APIs, so each test sets up
// only what it needs and clicks through only what it is testing. [F-12] #199.
//
// Every name the suite makes carries a per-run suffix, so a run never collides
// with an earlier one or with data already in a local database. The demo seed
// (#454) also builds on these helpers, and only it uses the fixed-name ones.
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { isBelow } from './credits'

const execFileAsync = promisify(execFile)

// The same variables the app is built with, and the same defaults. Exported
// for the demo seed's local-only guard (#454), which checks these and no copy.
export const AUTH_URL = process.env.VITE_API_URL ?? 'http://localhost:8000'
export const MARKET_URL = process.env.VITE_MARKET_API_URL ?? 'http://localhost:8001'
export const LEDGER_URL = process.env.VITE_LEDGER_API_URL ?? 'http://localhost:8003'

// Exported for the demo seed (#454), which asks compose which project its SQL
// step reaches.
export const REPO_ROOT = path.resolve(import.meta.dirname, '../../..')

export const PASSWORD = 'correct-horse-battery-staple'

export interface Session {
  id: string
  username: string
  accessToken: string
}

export interface Market {
  id: string
  question: string
  outcomes: { id: string; label: string }[]
  close_time: string
}

const RUN_ID = Math.random().toString(36).slice(2, 8)

/** A name unique to this run: letters, digits and underscores, as usernames allow. */
export function uniqueName(label: string): string {
  return `${label}_${RUN_ID}_${Math.random().toString(36).slice(2, 6)}`
}

async function call<T>(method: string, url: string, token: string | null, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!response.ok) {
    throw new Error(`${method} ${url} answered ${response.status}: ${await response.text()}`)
  }
  return (await response.json()) as T
}

interface AuthResponse {
  user: { id: string; username: string }
  tokens: { access_token: string }
}

export async function logInThroughApi(username: string): Promise<Session> {
  const body = await call<AuthResponse>('POST', `${AUTH_URL}/auth/login`, null, { identifier: username, password: PASSWORD })
  return { id: body.user.id, username: body.user.username, accessToken: body.tokens.access_token }
}

export async function registerTrader(label: string): Promise<Session> {
  return registerNamedTrader(uniqueName(label))
}

/** Registers exactly `username`, with no per-run suffix: the demo seed's fixed names (#454). */
export async function registerNamedTrader(username: string): Promise<Session> {
  const body = await call<AuthResponse>('POST', `${AUTH_URL}/auth/register`, null, {
    username,
    email: `${username}@example.com`,
    password: PASSWORD,
  })
  return { id: body.user.id, username: body.user.username, accessToken: body.tokens.access_token }
}

// The most any one trader in the suite spends: tp_other's 200 Yes shares in
// trading-panel.e2e.ts, on an untouched market at b = 100. A bigger buy
// anywhere raises it.
const LARGEST_SPEND = '143.3781'

let grantRead: Promise<string> | undefined

/**
 * What the ledger grants a new trader (ADR 0009), whatever STARTING_CREDITS
 * the stack was started with: `starting_credits` from the portfolio, which is
 * the grant actually minted (ledger-service.md). Read off a trader of its own,
 * never the one under test, so a test of the first read minting the grant
 * still makes that first read itself. Once per worker, unless the read
 * fails: a failure is not kept, so the next test reads again.
 */
export function startingGrant(): Promise<string> {
  if (!grantRead) {
    grantRead = readStartingGrant()
    grantRead.catch(() => {
      grantRead = undefined
    })
  }
  return grantRead
}

async function readStartingGrant(): Promise<string> {
  const probe = await registerTrader('grant_probe')
  const portfolio = await call<{ starting_credits: string }>('GET', `${LEDGER_URL}/ledger/portfolio/me`, probe.accessToken)
  const grant = portfolio.starting_credits
  if (isBelow(grant, LARGEST_SPEND)) {
    throw new Error(
      `STARTING_CREDITS is too small for the end-to-end suite: the ledger grants ${grant} credits, and ` +
        `one trader in the suite spends ${LARGEST_SPEND}. Set it to at least that and recreate the ledger.`,
    )
  }
  return grant
}

/**
 * Registers, then promotes by hand in the database, the way every first
 * admin is made (ADR 0007), then logs in again: the role rides in the token.
 */
export async function registerAdmin(label: string): Promise<Session> {
  const trader = await registerTrader(label)
  await promoteToAdmin(trader.username)
  return logInThroughApi(trader.username)
}


/**
 * Runs one UPDATE as the superuser, the way a first admin is made by hand, and
 * checks it changed exactly one row.
 *
 * Awaited, never blocking (#492): `docker compose exec` can take seconds on a
 * loaded machine, and a blocked event loop cannot see uvicorn close an idle
 * keep-alive socket, so the next fetch goes out on a dead one
 * (`UND_ERR_SOCKET`).
 */
async function runSql(sql: string, failure: string): Promise<void> {
  const { stdout } = await execFileAsync(
    'docker',
    ['compose', 'exec', '-T', 'db', 'psql', '-U', process.env.POSTGRES_USER ?? 'cs464', '-d', process.env.POSTGRES_DB ?? 'cs464', '-v', 'ON_ERROR_STOP=1', '-c', sql],
    { cwd: REPO_ROOT, encoding: 'utf8' },
  )
  if (!stdout.includes('UPDATE 1')) throw new Error(`${failure}: ${stdout}`)
}

export async function promoteToAdmin(username: string): Promise<void> {
  // Interpolated into SQL below, and only ever a name uniqueName() made or one
  // of the demo seed's fixed names.
  if (!/^\w+$/.test(username)) throw new Error(`refusing to promote ${username}`)
  await runSql(`UPDATE auth.users SET role = 'admin' WHERE lower(username) = lower('${username}')`, `promoting ${username} changed no row`)
}

/** Suspends an account ([4.2] #14), as an administrator through the auth service. */
export async function suspend(admin: Session, userId: string): Promise<void> {
  await call('PATCH', `${AUTH_URL}/admin/users/${userId}/suspension`, admin.accessToken, {
    suspended: true,
    reason: 'Suspended by the end-to-end test.',
  })
}

/**
 * Changes a user's role as an existing administrator ([4.4] #16), through the
 * auth service: how every administrator after the first is made (ADR 0007),
 * audited as `user.role_changed`. The role rides in the access token, so it
 * takes effect at the user's next login.
 */
export async function changeRole(admin: Session, userId: string, role: 'admin' | 'trader', reason: string): Promise<void> {
  await call('PATCH', `${AUTH_URL}/admin/users/${userId}/role`, admin.accessToken, { role, reason })
}

interface MarketOut {
  id: string
  question: string
  outcomes: { id: string; label: string }[]
  close_time: string
}

export const HOURS_PER_DAY = 24
const THIRTY_DAYS_IN_HOURS = 30 * HOURS_PER_DAY

function hoursFromNow(hours: number): string {
  return new Date(Date.now() + hours * 60 * 60 * 1000).toISOString()
}

/**
 * A market's terms beyond its question and outcomes. Every field has the
 * suite's own default; the demo seed (#454) names real-sounding ones.
 */
export interface MarketTerms {
  closesInHours?: number
  resolutionCriteria?: string
  resolutionSource?: { url: string; label: string }
}

async function save(
  admin: Session,
  question: string,
  status: 'draft' | 'submitted',
  outcomeLabels = ['Yes', 'No'],
  {
    closesInHours = THIRTY_DAYS_IN_HOURS,
    resolutionCriteria = 'Resolves YES if the end-to-end test says so.',
    resolutionSource = { url: 'https://example.com/source', label: 'Source' },
  }: MarketTerms = {},
): Promise<MarketOut> {
  const body = await call<{ market: MarketOut }>('POST', `${MARKET_URL}/markets`, admin.accessToken, {
    draft_key: randomUUID(),
    status,
    question,
    outcomes: outcomeLabels.map(label => ({ label })),
    close_time: hoursFromNow(closesInHours),
    resolution_time: hoursFromNow(closesInHours + 15 * HOURS_PER_DAY),
    resolution_criteria: resolutionCriteria,
    resolution_sources: [resolutionSource],
    // Pinned rather than left to the stack's DEFAULT_LIQUIDITY_B, so a test
    // can assert the exact price a trade moves the market to.
    liquidity_b: '100',
    seed_subsidy: 250,
  })
  return body.market
}

/** A draft, closing in thirty days unless told otherwise. Only its creator sees it ([1.1] #1). */
export async function saveDraft(admin: Session, question: string, options: { closesInHours?: number } = {}): Promise<Market> {
  return save(admin, question, 'draft', undefined, { closesInHours: options.closesInHours })
}

/** Submitted and not published, closing in thirty days unless told otherwise. Only its creator sees it ([1.1] #1). */
export async function submitMarket(admin: Session, question: string, options: { closesInHours?: number } = {}): Promise<Market> {
  return save(admin, question, 'submitted', undefined, { closesInHours: options.closesInHours })
}

/**
 * Submitted and published: open to traders, closing in thirty days unless told
 * otherwise. Outcomes Yes and No unless named.
 */
export async function publishMarket(
  admin: Session,
  question: string,
  outcomeLabels?: string[],
  terms: MarketTerms = {},
): Promise<Market> {
  const submitted = await save(admin, question, 'submitted', outcomeLabels, terms)
  return call<MarketOut>('POST', `${MARKET_URL}/markets/${submitted.id}/publish`, admin.accessToken)
}

/** Stops trading by hand ([2.3] #7), which writes CLOSED at once. */
export async function closeEarly(
  admin: Session,
  marketId: string,
  reason = 'Closed by the end-to-end test to propose an outcome.',
): Promise<void> {
  await call('POST', `${MARKET_URL}/markets/${marketId}/close`, admin.accessToken, { reason })
}

interface MarketWithProposal extends MarketOut {
  status: string
  proposal_id: string | null
}

/** Proposes a winner on a closed market ([3.1] #9). */
export async function proposeOutcome(
  admin: Session,
  market: Market,
  winningOutcomeLabel: string,
  evidenceNote = 'Decided by the end-to-end test to drive the market to approved.',
): Promise<MarketWithProposal> {
  const outcome = market.outcomes.find(candidate => candidate.label === winningOutcomeLabel)
  if (!outcome) throw new Error(`no outcome ${winningOutcomeLabel} on ${market.question}`)
  return call<MarketWithProposal>('POST', `${MARKET_URL}/markets/${market.id}/propose-outcome`, admin.accessToken, {
    winning_outcome_id: outcome.id,
    evidence_note: evidenceNote,
  })
}

/** Approves another administrator's proposal ([3.2] #10), moving the market to approved. */
export async function approveOutcome(admin: Session, marketId: string, proposalId: string | null): Promise<MarketWithProposal> {
  return call<MarketWithProposal>('POST', `${MARKET_URL}/markets/${marketId}/approve-outcome`, admin.accessToken, {
    proposal_id: proposalId,
  })
}

/**
 * Moves an approved market's stored dispute window into the past, by hand in
 * the database, the way `promoteToAdmin` makes the first admin. Settling also
 * needs SETTLEMENT_GAP (five hard-coded minutes, ADR 0019) to have passed
 * since the window's end, so this backdates past both — a test cannot wait
 * out either, so it reaches the stored column directly rather than the
 * setting or `approved_at`.
 */
export async function backdateDisputeWindow(marketId: string): Promise<void> {
  if (!/^[\w-]+$/.test(marketId)) throw new Error(`refusing to backdate ${marketId}`)
  await runSql(`UPDATE market.markets SET dispute_window_ends_at = now() - interval '10 minutes' WHERE id = '${marketId}'`, `backdating ${marketId} changed no row`)
}

/**
 * Settles an approved market whose dispute window has passed ([3.4] #12): the
 * ledger's own request, paying every winning holder (ADR 0019). The suite
 * settles through the browser in settle-market.e2e.ts; the demo seed (#454)
 * and balance-on-return.e2e.ts settle through this.
 */
export async function settleMarket(admin: Session, marketId: string): Promise<void> {
  await call('POST', `${LEDGER_URL}/ledger/markets/${marketId}/settlement`, admin.accessToken)
}

interface Snapshot {
  state_version: number
  prices: { outcome_id: string; price: string }[]
}

export async function snapshot(session: Session, marketId: string): Promise<Snapshot> {
  return call<Snapshot>('GET', `${LEDGER_URL}/ledger/markets/${marketId}/snapshot`, session.accessToken)
}

// A buy the grant cannot cover means LARGEST_SPEND above is out of date, not
// that the ledger is wrong — and `insufficient_funds` on its own points at the
// balance rather than at the configuration, which is the failure #373 is
// about, one layer along. Every other refusal is passed through untouched:
// close-market.e2e.ts matches `market_closed` on this message.
export function withGuardHint(refusal: unknown, quantity: string): unknown {
  if (!(refusal instanceof Error) || !refusal.message.includes('insufficient_funds')) return refusal
  return new Error(
    `${refusal.message}\nThis buy of ${quantity} shares costs more than the ledger grants a trader. ` +
      `Raise LARGEST_SPEND in e2e/support/backend.ts to what it costs, and STARTING_CREDITS to at least that.`,
  )
}

/** Buys `quantity` shares of one outcome at the current quote. */
export async function buy(trader: Session, market: Market, outcomeLabel: string, quantity: string): Promise<void> {
  const outcome = market.outcomes.find(candidate => candidate.label === outcomeLabel)
  if (!outcome) throw new Error(`no outcome ${outcomeLabel} on ${market.question}`)
  const { state_version } = await snapshot(trader, market.id)
  try {
    await call('POST', `${LEDGER_URL}/ledger/markets/${market.id}/trades`, trader.accessToken, {
      outcome_id: outcome.id,
      side: 'buy',
      quantity,
      state_version,
      idempotency_key: randomUUID(),
    })
  } catch (refusal) {
    throw withGuardHint(refusal, quantity)
  }
}
