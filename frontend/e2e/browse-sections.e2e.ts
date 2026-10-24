import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [X-1] #448: the browse list gains a few short curated bands above it, each
// named for the rule it was built from.
//
// The seam only an e2e test sees is Trending: the band ranks on
// `credits_traded`, which the ledger maintains under the book lock as trades
// commit (#447) and serves on the batch snapshot. A unit test fakes that
// figure, so nothing else checks that a real trade is what puts a real market
// in the band.
test('the browse list bands its markets, and a search collapses the bands', async ({ page }) => {
  const admin = await registerAdmin('bs_admin')
  // Closing within the hour so soonest-first keeps both on the first page
  // however many markets earlier runs left open ([X-1] #104 paged the list).
  const traded = await publishMarket(
    admin,
    `Will ${uniqueName('bs_hot')} be traded?`,
    ['Lakers', 'Celtics'],
    { closesInHours: 1 },
  )
  const trader = await registerTrader('bs_trader')

  // The trade is what the band is about: before it, `credits_traded` is zero
  // and the market is in no band at all.
  await buy(trader, traded, 'Lakers', '50.0000')

  await logIn(page, trader)
  await page.goto('/markets')

  // A hero at the top, the bands under it, and the full list still below
  // them. Every band states its own rule (AC 2).
  await expect(page.getByRole('heading', { name: 'Featured' })).toBeVisible()
  await expect(page.getByText('Most traded right now')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Trending' })).toBeVisible()
  await expect(page.getByText('Most credits traded')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Browse all' })).toBeVisible()

  // The hero is one market, not a band of them.
  const featuredBand = page.locator('section', { has: page.getByRole('heading', { name: 'Featured' }) })
  await expect(featuredBand.getByRole('link')).toHaveCount(1)

  // Trending holds real markets, ranked on a figure the ledger maintains as
  // trades commit. Which markets is not this test's business: every run leaves
  // its own traded markets behind, and the band holds the three busiest of
  // them — so asserting that *this* market ranks would be asserting a total,
  // which this suite never does.
  const trendingBand = page.locator('section', { has: page.getByRole('heading', { name: 'Trending' }) })
  await expect(trendingBand.getByRole('link').first()).toBeVisible()

  // Closing soon holds markets too, and every market in a band is a market
  // the list below also holds: a band is a shortcut into the list, not a
  // partition of it (AC 5). Checked through the band's own first card, whose
  // question is then looked for outside every band.
  const closingBand = page.locator('section', { has: page.getByRole('heading', { name: 'Closing soon' }) })
  const bandedQuestion = await closingBand.getByRole('link').first().locator('p').first().innerText()
  const fullList = page.locator('section', { has: page.getByRole('heading', { name: 'Browse all' }) })
  await expect(fullList.getByText(bandedQuestion, { exact: true })).toBeVisible()

  // And this run's own market is on the page at all, banded or not.
  await expect(marketCard(page, traded.question).first()).toBeVisible()

  // A search collapses to one plain result list (AC 4), while still finding
  // the market — this drops the bands, not the results.
  await page.getByRole('searchbox', { name: /search markets/i }).fill('bs_hot');
  await page.getByRole('searchbox', { name: /search markets/i }).press('Enter')

  await expect(page.getByRole('heading', { name: 'Featured' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Trending' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Browse all' })).toHaveCount(0)
  // The result is still found: this collapsed the bands, not the results.
  await expect(page.getByText(traded.question, { exact: true })).toBeVisible()
})
