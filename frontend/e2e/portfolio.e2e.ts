import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// [T-4] #24, through the real ledger and market_service: the portfolio
// route carries ids only, and the page's own join against
// GET /public/markets/{id} is exactly what unit tests against MSW fakes
// cannot catch.
test('a trader sees their position, with the market question and outcome label joined in', async ({ page }) => {
  const admin = await registerAdmin('pf_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('portfolio')} resolve yes?`)
  const trader = await registerTrader('pf_trader')
  await buy(trader, market, 'Yes', '5.0000')

  await logIn(page, trader)
  await page.goto('/portfolio')

  await expect(page.getByText(market.question)).toBeVisible()
  await expect(page.getByText('Yes', { exact: true })).toBeVisible()
  await expect(page.getByText('5.0000')).toBeVisible()
  // liquidity_b is 100 and nothing else traded against this market, so these
  // are exact, not just present — the one test that talks to the real ledger.
  await expect(page.getByText('997.4687 credits')).toBeVisible()
  await expect(page.getByText('2.5312 credits')).toBeVisible()
  await expect(page.getByText('999.9999 credits')).toBeVisible()
  await expect(page.getByText('2.5313')).toBeVisible()
  await expect(page.getByText('-0.0001')).toBeVisible()
})

test('a trader with no positions sees the empty state', async ({ page }) => {
  const trader = await registerTrader('pf_empty')

  await logIn(page, trader)
  await page.goto('/portfolio')

  await expect(page.getByText(/no open positions/i)).toBeVisible()
  // A brand-new trader's balance is granted on first read (ADR 0009).
  await expect(page.getByText('1,000.0000 credits')).toHaveCount(2)
})
