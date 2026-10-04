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
  await expect(page.getByText('Cash')).toBeVisible()
  await expect(page.getByText('Net worth')).toBeVisible()
})

test('a trader with no positions sees the empty state', async ({ page }) => {
  const trader = await registerTrader('pf_empty')

  await logIn(page, trader)
  await page.goto('/portfolio')

  await expect(page.getByText(/no open positions/i)).toBeVisible()
})
