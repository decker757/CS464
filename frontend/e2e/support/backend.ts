// Test data, made through the backend's own HTTP APIs, so each test sets up
// only what it needs and clicks through only what it is testing. [F-12] #199.
//
// Every name carries a per-run suffix, so a run never collides with an earlier
// one or with data already in a local database.
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import path from 'node:path'

// The same variables the app is built with, and the same defaults.
const AUTH_URL = process.env.VITE_API_URL ?? 'http://localhost:8000'
const MARKET_URL = process.env.VITE_MARKET_API_URL ?? 'http://localhost:8001'
const LEDGER_URL = process.env.VITE_LEDGER_API_URL ?? 'http://localhost:8003'

const REPO_ROOT = path.resolve(import.meta.dirname, '../../..')

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

async function logInThroughApi(username: string): Promise<Session> {
  const body = await call<AuthResponse>('POST', `${AUTH_URL}/auth/login`, null, { identifier: username, password: PASSWORD })
  return { id: body.user.id, username: body.user.username, accessToken: body.tokens.access_token }
}

export async function registerTrader(label: string): Promise<Session> {
  const username = uniqueName(label)
  const body = await call<AuthResponse>('POST', `${AUTH_URL}/auth/register`, null, {
    username,
    email: `${username}@example.com`,
    password: PASSWORD,
  })
  return { id: body.user.id, username: body.user.username, accessToken: body.tokens.access_token }
}

/**
 * Registers, then promotes by hand in the database, the way every first
 * admin is made (ADR 0007), then logs in again: the role rides in the token.
 */
export async function registerAdmin(label: string): Promise<Session> {
  const trader = await registerTrader(label)
  promoteToAdmin(trader.username)
  return logInThroughApi(trader.username)
}

function promoteToAdmin(username: string): void {
  // Interpolated into SQL below, and only ever a name uniqueName() made.
  if (!/^\w+$/.test(username)) throw new Error(`refusing to promote ${username}`)
  const sql = `UPDATE auth.users SET role = 'admin' WHERE lower(username) = lower('${username}')`
  const output = execFileSync(
    'docker',
    ['compose', 'exec', '-T', 'db', 'psql', '-U', process.env.POSTGRES_USER ?? 'cs464', '-d', process.env.POSTGRES_DB ?? 'cs464', '-v', 'ON_ERROR_STOP=1', '-c', sql],
    { cwd: REPO_ROOT, encoding: 'utf8' },
  )
  if (!output.includes('UPDATE 1')) throw new Error(`promoting ${username} changed no row: ${output}`)
}

interface MarketOut {
  id: string
  question: string
  outcomes: { id: string; label: string }[]
}

function daysFromNow(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString()
}

async function save(admin: Session, question: string, status: 'draft' | 'submitted', outcomeLabels = ['Yes', 'No']): Promise<MarketOut> {
  const body = await call<{ market: MarketOut }>('POST', `${MARKET_URL}/markets`, admin.accessToken, {
    draft_key: randomUUID(),
    status,
    question,
    outcomes: outcomeLabels.map(label => ({ label })),
    close_time: daysFromNow(30),
    resolution_time: daysFromNow(45),
    resolution_criteria: 'Resolves YES if the end-to-end test says so.',
    resolution_sources: [{ url: 'https://example.com/source', label: 'Source' }],
    // Pinned rather than left to the stack's DEFAULT_LIQUIDITY_B, so a test
    // can assert the exact price a trade moves the market to.
    liquidity_b: '100',
    seed_subsidy: 250,
  })
  return body.market
}

export async function saveDraft(admin: Session, question: string): Promise<Market> {
  return save(admin, question, 'draft')
}

/** Submitted and published: open to traders, closing in thirty days. Outcomes Yes and No unless named. */
export async function publishMarket(admin: Session, question: string, outcomeLabels?: string[]): Promise<Market> {
  const submitted = await save(admin, question, 'submitted', outcomeLabels)
  return call<MarketOut>('POST', `${MARKET_URL}/markets/${submitted.id}/publish`, admin.accessToken)
}

/** Stops trading by hand ([2.3] #7), which writes CLOSED at once. */
export async function closeEarly(admin: Session, marketId: string): Promise<void> {
  await call('POST', `${MARKET_URL}/markets/${marketId}/close`, admin.accessToken, {
    reason: 'Closed by the end-to-end test to propose an outcome.',
  })
}

interface Snapshot {
  state_version: number
  prices: { outcome_id: string; price: string }[]
}

export async function snapshot(session: Session, marketId: string): Promise<Snapshot> {
  return call<Snapshot>('GET', `${LEDGER_URL}/ledger/markets/${marketId}/snapshot`, session.accessToken)
}

/** Buys `quantity` shares of one outcome at the current quote. */
export async function buy(trader: Session, market: Market, outcomeLabel: string, quantity: string): Promise<void> {
  const outcome = market.outcomes.find(candidate => candidate.label === outcomeLabel)
  if (!outcome) throw new Error(`no outcome ${outcomeLabel} on ${market.question}`)
  const { state_version } = await snapshot(trader, market.id)
  await call('POST', `${LEDGER_URL}/ledger/markets/${market.id}/trades`, trader.accessToken, {
    outcome_id: outcome.id,
    side: 'buy',
    quantity,
    state_version,
    idempotency_key: randomUUID(),
  })
}
