import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { loadUntilShown, logIn } from './support/browser'

// [FE][2.4] #8, through the real ledger's #63 endpoints. A unit test fakes
// GET .../price-history and GET .../trades with MSW, so a renamed field
// there would still pass; this is the one test that talks to both for real.
test('an admin sees a trade in the price history and the trade log', async ({ page }) => {
  const admin = await registerAdmin('ph_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('pricehistory')} resolve yes?`)
  const trader = await registerTrader('ph_trader')
  await buy(trader, market, 'Yes', '5')

  await logIn(page, admin)
  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('link', { name: 'Price History' }).click()

  await expect(page.getByText(market.question)).toBeVisible()
  const rows = page.getByRole('row')
  // Newest first on the trade log; row 0 is the header.
  await expect(rows.nth(1)).toContainText('Buy')
  await expect(rows.nth(1)).toContainText('5.0000')
})
