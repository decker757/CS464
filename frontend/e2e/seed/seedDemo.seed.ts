// #454. Seeds a demo database: real-sounding SMU markets, traded apart over a
// few minutes, with one market at each later stage of its life. `npm run
// seed:demo`, against a stack on this machine; README.md says how.
//
// It runs as a Playwright test only to use the runtime the e2e helpers already
// run under, and it makes everything through those helpers: the services' own
// APIs, plus the two SQL steps they already take (promoting the first admin,
// backdating a dispute window).
//
// One-shot. Its users have fixed names, so a second run against the same
// database stops at the first registration and changes nothing.
import { execFileSync } from 'node:child_process'
import { test } from '@playwright/test'
import {
  approveOutcome,
  AUTH_URL,
  backdateDisputeWindow,
  buy,
  closeEarly,
  LEDGER_URL,
  MARKET_URL,
  proposeOutcome,
  publishMarket,
  registerNamedAdmin,
  registerNamedTrader,
  settleMarket,
  snapshot,
  type Market,
  type Session,
} from '../support/backend'
import { ADMINS, DEMO_MARKETS, TRADERS, type DemoMarket, type DemoTrade } from './demoMarkets'
import { refuseUnlessLocal } from './localTarget'
import { inRounds } from './rounds'

const HOURS_PER_DAY = 24

// Long enough apart that the price chart has a shape on its time axis; short
// enough that the whole seed takes a few minutes. A trade cannot be backdated
// through the API, so real time is the only honest way to spread them.
const PAUSE_BETWEEN_ROUNDS_MS = 20_000

/** The engine `docker compose exec` will reach: DOCKER_HOST if set, otherwise the current context's. */
function dockerEndpoint(): string {
  if (process.env.DOCKER_HOST) return process.env.DOCKER_HOST
  return execFileSync('docker', ['context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], { encoding: 'utf8' }).trim()
}

async function registerFirstAdmin(username: string): Promise<Session> {
  try {
    return await registerNamedAdmin(username)
  } catch (refusal) {
    if (refusal instanceof Error && refusal.message.includes(' answered 409')) {
      throw new Error(
        `${username} already exists, so this database has been seeded before. The seed is one-shot: ` +
          'start from an empty database to seed again.',
      )
    }
    throw refusal
  }
}

// Settlement is safe to repeat: a repeat writes nothing and answers the same
// 200 (ledger-service.md, "A repeat writes nothing here"). So one retry covers
// a network failure, which the first run of this seed met: the backdate takes
// a few seconds, and the ledger closed the idle keep-alive connection just as
// the settlement was sent on it.
async function settle(admin: Session, marketId: string): Promise<void> {
  try {
    await settleMarket(admin, marketId)
  } catch (failure) {
    if (!(failure instanceof TypeError)) throw failure
    await settleMarket(admin, marketId)
  }
}

async function driveToItsEnd(demo: DemoMarket, market: Market, creator: Session, approver: Session): Promise<void> {
  if (demo.endsAs === 'open') return
  await closeEarly(creator, market.id, demo.closeReason)
  if (demo.endsAs === 'closed') return
  // The proposer is always the market's creator, and the approver must be a
  // second administrator (ADR 0016).
  const proposed = await proposeOutcome(creator, market, demo.winner, demo.evidenceNote)
  if (demo.endsAs === 'pending_resolution') return
  await approveOutcome(approver, market.id, proposed.proposal_id)
  if (demo.endsAs === 'approved') return
  backdateDisputeWindow(market.id)
  await settle(creator, market.id)
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

  const creator = await registerFirstAdmin(ADMINS.creator)
  const approver = await registerNamedAdmin(ADMINS.approver)
  const traders = new Map<string, Session>()
  for (const username of TRADERS) traders.set(username, await registerNamedTrader(username))
  console.log(`Registered ${ADMINS.creator} and ${ADMINS.approver} (admins) and ${TRADERS.length} traders.`)

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

  for (const [index, demo] of DEMO_MARKETS.entries()) {
    await driveToItsEnd(demo, markets[index], creator, approver)
  }

  console.log('\nWhere every market ended up:')
  for (const [index, demo] of DEMO_MARKETS.entries()) {
    const { prices } = await snapshot(creator, markets[index].id)
    console.log(priceLine(demo, markets[index], prices))
  }
})
