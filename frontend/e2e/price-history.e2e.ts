import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard, setQueryParams } from './support/browser'

// [FE][2.4] #8, through the real ledger's #63 endpoints. A unit test fakes
// GET .../price-history and GET .../trades with MSW, so a renamed field
// there would still pass; this is the one test that talks to both for real.
test('an admin sees a trade in the price history and the trade log', async ({ page }) => {
  const admin = await registerAdmin('ph_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('pricehistory')} resolve yes?`)
  const trader = await registerTrader('ph_trader')
  await buy(trader, market, 'Yes', '5')

  await logIn(page, admin)
  await setQueryParams(page, '/markets/overview', { q: market.question })
  await page.goto('/admin/markets')
  const card = marketCard(page, market.question)
  await card.getByRole('link', { name: 'Price History' }).click()

  await expect(page.getByText(market.question)).toBeVisible()

  // The chart drew one line per outcome. This has to be waited on first:
  // the two "not showing" checks below are both true while the request is
  // still in flight, so on their own they pass with the chart broken.
  await expect(page.locator('.recharts-line-curve')).toHaveCount(market.outcomes.length)
  await expect(page.getByText('No price history yet.')).toHaveCount(0)
  await expect(page.getByText('Failed to load price history.')).toHaveCount(0)

  const rows = page.getByRole('row')
  // Newest first on the trade log; row 0 is the header.
  const newestTrade = rows.nth(1)
  await expect(newestTrade).toContainText('Buy')
  await expect(newestTrade).toContainText('5.0000')
  // The user column names the trader, joined in from auth's lookup
  // (MarketTradeTable, #434); trade-log-usernames.e2e.ts owns that join.
  await expect(newestTrade).toContainText(trader.username)
  // The date column read a field the ledger really sends.
  const dateCell = newestTrade.getByRole('cell').first()
  await expect(dateCell).not.toContainText('Invalid Date')
  await expect(dateCell).toHaveText(/\d{1,2}\/\d{1,2}\/\d{4}/)
})

// [FE][T-5] #453. The same route, read from the market page with a trader's
// cookie. ledger-service.md opens it to any role, and only the real ledger can
// show that a trader is answered rather than refused as on an admin route; the
// lines also prove the chart's own chunk loaded from the built bundle.
test('a trader sees the price history on the market page', async ({ page }) => {
  const admin = await registerAdmin('phm_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('pricechart')} resolve yes?`)
  const trader = await registerTrader('phm_trader')
  // The book's opening and this trade: two points, so there is a line to draw.
  await buy(trader, market, 'Yes', '5')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  // Waited on first, for the same reason as above.
  await expect(page.locator('.recharts-line-curve')).toHaveCount(market.outcomes.length)
  await expect(page.getByText('Failed to load price history.')).toHaveCount(0)
})

// [FE][T-5] #453. A trader with the app open across a deploy asks for a chart
// chunk the server no longer has. Only a real browser loading the built bundle
// can fail that download; the market page must still trade.
test('a chart chunk that fails to download leaves the market page trading', async ({ page }) => {
  const admin = await registerAdmin('phf_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('chartfail')} resolve yes?`)
  const trader = await registerTrader('phf_trader')
  // Two points, so the page asks for the chart rather than saying no trades.
  await buy(trader, market, 'Yes', '5')

  await page.route('**/PriceHistoryChart-*.js', route => route.abort())
  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  await expect(page.getByText('Price history unavailable. Reload the page to try again.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Buy Yes', exact: true })).toBeVisible()
})
