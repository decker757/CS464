// #454. Seeds a demo database: real-sounding SMU markets, traded apart over a
// few minutes, with one market at each later stage of its life, a voided one,
// a few resting limit orders and a suspended trader (#570). `npm run
// seed:demo`, against a stack on this machine; README.md says how.
//
// It runs as a Playwright test only to use the runtime the e2e helpers already
// run under, and it makes everything through those helpers: the services' own
// APIs, plus the two SQL steps they already take (promoting the first admin,
// backdating a dispute window). The second admin is promoted by the first,
// through the role-change endpoint, as every admin after the first is.
//
// One-shot. Its users have fixed names, so a second run against the same
// database stops at the first registration and changes nothing. A run that
// stops partway leaves those names taken, so it needs a fresh database too.
import { execFileSync } from 'node:child_process'
import { test } from '@playwright/test'
import {
  approveOutcome,
  AUTH_URL,
  backdateDisputeWindow,
  buy,
  changeRole,
  closeEarly,
  HOURS_PER_DAY,
  LEDGER_URL,
  logInThroughApi,
  MARKET_URL,
  placeLimitBuy,
  promoteToAdmin,
  proposeOutcome,
  publishMarket,
  registerNamedTrader,
  REPO_ROOT,
  settleMarket,
  snapshot,
  suspend,
  voidMarket,
  type Market,
  type Session,
} from '../support/backend'
import { ADMINS, DEMO_MARKETS, ORDER_TRADER, SUSPENDED, TRADERS, type DemoMarket, type DemoTrade } from './demoMarkets'
import { refuseDevComposeProject, refuseUnlessLocal } from './localTarget'
import { refuseUnlessAdmin } from './promotion'
import { inRounds } from './rounds'

// Long enough apart that the price chart has a shape on its time axis; short
// enough that the whole seed takes a few minutes. A trade cannot be backdated
// through the API, so real time is the only honest way to spread them.
const PAUSE_BETWEEN_ROUNDS_MS = 20_000

/** The engine `docker compose exec` will reach: DOCKER_HOST if set, otherwise the current context's. */
function dockerEndpoint(): string {
  if (process.env.DOCKER_HOST) return process.env.DOCKER_HOST
  return execFileSync('docker', ['context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], { encoding: 'utf8' }).trim()
}

/** The compose project `docker compose exec db` reaches from the repository root. Reads no secret: nothing is interpolated. */
function composeProject(): string {
  const config = execFileSync('docker', ['compose', 'config', '--no-interpolate', '--format', 'json'], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
  })
  return JSON.parse(config).name
}

async function registerTraderUnlessSeeded(username: string): Promise<Session> {
  try {
    return await registerNamedTrader(username)
  } catch (refusal) {
    // The auth service's `409 duplicate_user`, carried in the helper's message.
    if (refusal instanceof Error && refusal.message.includes('duplicate_user')) {
      throw new Error(
        `${username} already exists, so this database has been seeded before, or an earlier run stopped ` +
          'partway. The seed is one-shot: start from an empty database to seed again.',
      )
    }
    throw refusal
  }
}

/**
 * Registers, promotes by hand the way a first admin is made (ADR 0007), and
 * logs in again. Then checks the new token really is an admin's: the SQL step
 * may have reached another compose project than the URLs.
 */
async function registerFirstAdmin(username: string, project: string): Promise<Session> {
  const trader = await registerTraderUnlessSeeded(username)
  await promoteToAdmin(trader.username)
  const admin = await logInThroughApi(trader.username)
  refuseUnlessAdmin(admin.accessToken, project)
  return admin
}

/**
 * Registers, and has an existing admin promote them through the role-change
 * endpoint, the way every admin after the first is made (root CLAUDE.md, ADR
 * 0007), so the audit log names who made them one. Then logs in again: the
 * role rides in the token.
 */
async function registerPromotedAdmin(promoter: Session, username: string): Promise<Session> {
  const trader = await registerTraderUnlessSeeded(username)
  await changeRole(promoter, trader.id, 'admin', 'Second administrator for the showcase, to approve proposed outcomes.')
  return logInThroughApi(trader.username)
}

// Settlement and a void both move the money first and tell market_service
// last, and both are safe to repeat: a repeat writes nothing (ledger-service.md,
// "A repeat writes nothing here"). `503 settlement_unconfirmed` and `503
// void_unconfirmed` mean only that last step failed, and the same doc says to
// repeat the request. Without this, one blip ends a one-shot run that needs a
// fresh database to start again.
async function repeatOnceIfUnconfirmed(unconfirmedCode: string, request: () => Promise<void>): Promise<void> {
  try {
    await request()
  } catch (failure) {
    const isUnconfirmed = failure instanceof Error && failure.message.includes(unconfirmedCode)
    if (!isUnconfirmed) throw failure
    await request()
  }
}

async function driveToItsEnd(demo: DemoMarket, market: Market, creator: Session, approver: Session): Promise<void> {
  if (demo.endsAs === 'open') return
  await closeEarly(creator, market.id, demo.closeReason)
  if (demo.endsAs === 'closed') return
  // A void needs a closed market and an administrator with no stake in it
  // (ADR 0025); the creator never trades.
  if (demo.endsAs === 'voided') {
    await repeatOnceIfUnconfirmed('void_unconfirmed', () => voidMarket(creator, market.id, demo.voidReason))
    return
  }
  // The proposer is always the market's creator, and the approver must be a
  // second administrator (ADR 0016).
  const proposed = await proposeOutcome(creator, market, demo.winner, demo.evidenceNote)
  if (demo.endsAs === 'pending_resolution') return
  await approveOutcome(approver, market.id, proposed.proposal_id)
  if (demo.endsAs === 'approved') return
  await backdateDisputeWindow(market.id)
  await repeatOnceIfUnconfirmed('settlement_unconfirmed', () => settleMarket(creator, market.id))
}

function priceLine(demo: DemoMarket, market: Market, prices: { outcome_id: string; price: string }[]): string {
  const parts: string[] = []
  for (const outcome of market.outcomes) {
    const quoted = prices.find(price => price.outcome_id === outcome.id)
    parts.push(`${outcome.label} ${quoted?.price ?? '?'}`)
  }
  return `${demo.endsAs.padEnd(18)} ${parts.join(' / ').padEnd(56)} ${demo.question}`
}

test('seed the demo database', async () => {
  test.setTimeout(30 * 60 * 1000)
  refuseUnlessLocal({ serviceUrls: [AUTH_URL, MARKET_URL, LEDGER_URL], dockerEndpoint: dockerEndpoint() })
  const project = composeProject()
  refuseDevComposeProject(project)

  const creator = await registerFirstAdmin(ADMINS.creator, project)
  const approver = await registerPromotedAdmin(creator, ADMINS.approver)
  const traders = new Map<string, Session>()
  for (const username of TRADERS) traders.set(username, await registerTraderUnlessSeeded(username))
  console.log(
    `Registered ${ADMINS.creator} (admin, by hand), ${ADMINS.approver} (admin, promoted by ${ADMINS.creator}) ` +
      `and ${TRADERS.length} traders.`,
  )

  const markets: Market[] = []
  for (const demo of DEMO_MARKETS) {
    markets.push(
      await publishMarket(creator, demo.question, demo.outcomes, {
        closesInHours: demo.closesInDays * HOURS_PER_DAY,
        resolutionCriteria: demo.resolutionCriteria,
        resolutionSource: demo.resolutionSource,
      }),
    )
  }
  console.log(`Published ${markets.length} markets.`)

  const tradesByMarket: { market: Market; trade: DemoTrade }[][] = []
  DEMO_MARKETS.forEach((demo, index) => {
    tradesByMarket.push(demo.trades.map(trade => ({ market: markets[index], trade })))
  })
  const rounds = inRounds(tradesByMarket)
  for (const [index, round] of rounds.entries()) {
    for (const { market, trade } of round) {
      const trader = traders.get(trade.trader)
      if (!trader) throw new Error(`${trade.trader} is not one of the demo traders`)
      await buy(trader, market, trade.outcome, trade.quantity)
    }
    console.log(`Round ${index + 1} of ${rounds.length}: ${round.length} trades.`)
    if (index < rounds.length - 1) await new Promise(resolve => setTimeout(resolve, PAUSE_BETWEEN_ROUNDS_MS))
  }

  // After the rounds, so each limit is measured against the price the trades
  // left. One that fills on arrival means the trades above changed and the
  // limit no longer sits below the price: stop rather than seed an order
  // that will never be there to fill during the demo.
  const orderTrader = traders.get(ORDER_TRADER)
  if (!orderTrader) throw new Error(`${ORDER_TRADER} is not one of the demo traders`)
  for (const [index, demo] of DEMO_MARKETS.entries()) {
    if (demo.endsAs !== 'open' || !demo.restingOrder) continue
    const { outcome, limitPrice, quantity } = demo.restingOrder
    const placed = await placeLimitBuy(orderTrader, markets[index], outcome, { limitPrice, quantity })
    if (placed.fills.length > 0 || placed.order.status !== 'open') {
      throw new Error(`${ORDER_TRADER}'s order on "${demo.question}" filled on arrival instead of resting`)
    }
    console.log(`${ORDER_TRADER} rests a buy of ${quantity} ${outcome} at ${limitPrice} on "${demo.question}".`)
  }

  for (const [index, demo] of DEMO_MARKETS.entries()) {
    await driveToItsEnd(demo, markets[index], creator, approver)
  }

  // Last, so the account has traded and been paid like any other first.
  const suspended = traders.get(SUSPENDED.trader)
  if (!suspended) throw new Error(`${SUSPENDED.trader} is not one of the demo traders`)
  await suspend(creator, suspended.id, SUSPENDED.reason)
  console.log(`Suspended ${SUSPENDED.trader}.`)

  console.log('\nWhere every market ended up:')
  for (const [index, demo] of DEMO_MARKETS.entries()) {
    const { prices } = await snapshot(creator, markets[index].id)
    console.log(priceLine(demo, markets[index], prices))
  }
})
