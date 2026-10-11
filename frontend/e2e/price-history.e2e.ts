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

  // The chart got points: wait until it stops loading, then neither the
  // empty nor the failed message is showing.
  await expect(page.getByText('Loading price history…')).toHaveCount(0)
  await expect(page.getByText('No price history yet.')).toHaveCount(0)
  await expect(page.getByText('Failed to load price history.')).toHaveCount(0)

  const rows = page.getByRole('row')
  // Newest first on the trade log; row 0 is the header.
  const newestTrade = rows.nth(1)
  await expect(newestTrade).toContainText('Buy')
  await expect(newestTrade).toContainText('5.0000')
  // The user column is the trader's id, cut short (MarketTradeTable).
  await expect(newestTrade).toContainText(trader.id.slice(0, 8))
  // The date column read a field the ledger really sends.
  const dateCell = newestTrade.getByRole('cell').first()
  await expect(dateCell).not.toContainText('Invalid Date')
  await expect(dateCell).toHaveText(/\d{1,2}\/\d{1,2}\/\d{4}/)
})
