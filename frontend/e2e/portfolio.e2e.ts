import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, startingGrant, uniqueName } from './support/backend'
import { logIn } from './support/browser'
import { creditsLess, formatPreciseCredits } from './support/credits'

// [T-4] #24, through the real ledger and market_service: the portfolio
// route carries ids only, and the page's own join against
// GET /public/markets/{id} is exactly what unit tests against MSW fakes
// cannot catch.
test('a trader sees their position, with the market question and outcome label joined in', async ({ page }) => {
  const grant = await startingGrant()
  const admin = await registerAdmin('pf_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('portfolio')} resolve yes?`)
  const trader = await registerTrader('pf_trader')
  await buy(trader, market, 'Yes', '5.0000')

  await logIn(page, trader)
  await page.goto('/portfolio')

  await expect(page.getByText(market.question)).toBeVisible()
  await expect(page.getByText('Yes', { exact: true })).toBeVisible()
  await expect(page.getByText('5.0000', { exact: true })).toBeVisible()
  // liquidity_b is 100 and nothing else traded against this market, so these
  // are exact, not just present — the one test that talks to the real ledger.
  // Cash is the grant less the 2.5313 the buy cost, and net worth that cash
  // plus the 2.5312 the shares are worth. Every figure is matched whole: with
  // some grants one is the tail of another, "1,002.5312 credits" of
  // "2.5312 credits".
  const cash = formatPreciseCredits(creditsLess(grant, '2.5313'))
  const netWorth = formatPreciseCredits(creditsLess(grant, '0.0001'))
  await expect(page.getByText(`${cash} credits`, { exact: true })).toBeVisible()
  await expect(page.getByText('2.5312 credits', { exact: true })).toBeVisible()
  await expect(page.getByText(`${netWorth} credits`, { exact: true })).toBeVisible()
  await expect(page.getByText('2.5313', { exact: true })).toBeVisible()
  await expect(page.getByText('-0.0001', { exact: true })).toBeVisible()
  // [B-1] #376, against the real ledger's own sums: net worth is the grant
  // less 0.0001, so the trader is down 0.0001 — all of it still riding on
  // the position, none of it banked. Exact names, exact figures,
  // which is what a unit test against an MSW fake cannot check.
  await expect(page.getByText('-0.0001 credits', { exact: true })).toBeVisible()
  await expect(page.getByText('Realized 0.0000', { exact: true })).toBeVisible()
  await expect(page.getByText('Unrealized -0.0001', { exact: true })).toBeVisible()
})

test('a trader with no positions sees the empty state', async ({ page }) => {
  const grant = await startingGrant()
  const trader = await registerTrader('pf_empty')

  await logIn(page, trader)
  await page.goto('/portfolio')

  await expect(page.getByText(/no open positions/i)).toBeVisible()
  // A brand-new trader's balance is granted on first read (ADR 0009): cash
  // and net worth are both the grant.
  await expect(page.getByText(`${formatPreciseCredits(grant)} credits`, { exact: true })).toHaveCount(2)
  // And reads as flat against that grant, not as a loss: the ledger sends
  // total_pnl exactly 0.0000, so the figure carries no sign. The two zeroes
  // are the positions value and the total — exact, because "0.0000 credits"
  // is also the tail of a grant like "1,000.0000 credits". [B-1] #376
  await expect(page.getByText('0.0000 credits', { exact: true })).toHaveCount(2)
  await expect(page.getByText('+0.0000 credits', { exact: true })).toHaveCount(0)
})
