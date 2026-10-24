import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, startingGrant, uniqueName } from './support/backend'
import { logIn } from './support/browser'
import { creditsLess, formatRoundedCredits } from './support/credits'

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
  // The balance is the grant less the 2.5313 the buy cost, and net worth that
  // balance plus the 2.5312 the shares are worth. Every figure is matched
  // whole: with some grants one is the tail of another, "1,002.5312 credits"
  // of "2.5312 credits".
  //
  // Read through the page rather than the whole screen, because the bar now
  // shows the balance exactly as the card does — which is #443's point, and
  // what `balance.e2e.ts` checks on its own.
  const portfolio = page.getByRole('main')
  const balance = formatRoundedCredits(creditsLess(grant, '2.5313'))
  const netWorth = formatRoundedCredits(creditsLess(grant, '0.0001'))
  await expect(portfolio.getByText(`${balance} credits`, { exact: true })).toBeVisible()
  await expect(portfolio.getByText('2.53 credits', { exact: true })).toBeVisible()
  await expect(portfolio.getByText(`${netWorth} credits`, { exact: true })).toBeVisible()
  // The position's own row keeps the ledger's four places, where the cards
  // above are rounded to the cent (#443): both formats, on one screen,
  // against figures the ledger really computed.
  await expect(portfolio.getByText('2.5313', { exact: true })).toBeVisible()
  await expect(portfolio.getByText('-0.0001', { exact: true })).toBeVisible()
  // [B-1] #376, against the real ledger's own sums: net worth is the grant
  // less 0.0001, so the trader is down 0.0001 — all of it still riding on
  // the position, none of it banked. A position is valued at liquidation
  // (ADR 0018), so a fresh one is always that dust below what it cost: flat
  // at the cent, and shown as a plain zero rather than "-0" (#443). The
  // exact figure is in the row above.
  await expect(portfolio.getByText('0 credits', { exact: true })).toBeVisible()
  await expect(portfolio.getByText('Realized 0', { exact: true })).toBeVisible()
  await expect(portfolio.getByText('Unrealized 0', { exact: true })).toBeVisible()
})

test('a trader with no positions is taken from the empty state into the markets', async ({ page }) => {
  const grant = await startingGrant()
  const trader = await registerTrader('pf_empty')

  await logIn(page, trader)
  await page.goto('/portfolio')

  // A brand-new trader's balance is granted on first read (ADR 0009), and
  // nothing is riding on a position yet, so the bar and both cards show that
  // one figure. #443 had the bar truncating where the cards did not, which
  // is what makes this worth asserting in all three places at once.
  const granted = `${formatRoundedCredits(grant)} credits`
  await expect(page.getByLabel('available balance')).toHaveText(granted)
  await expect(page.getByRole('main').getByText(granted, { exact: true })).toHaveCount(2)
  // And reads as flat against that grant, not as a loss: the ledger sends
  // total_pnl exactly 0.0000, so the figure carries no sign. The two zeroes
  // are the positions value and the total. [B-1] #376
  await expect(page.getByText('0 credits', { exact: true })).toHaveCount(2)
  await expect(page.getByText('+0 credits', { exact: true })).toHaveCount(0)

  // The one person who sees this screen empty is the one who has never
  // traded, and the empty state used to name the markets page in plain text
  // rather than take them there (#443).
  await expect(page.getByText(/no open positions/i)).toBeVisible()
  await page.getByRole('link', { name: /browse markets/i }).click()

  await expect(page).toHaveURL(/\/markets$/)
  await expect(page.getByRole('heading', { name: 'Markets' })).toBeVisible()
})
