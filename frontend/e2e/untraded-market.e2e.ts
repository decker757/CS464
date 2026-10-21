import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [FE][X-1] #445, through the real ledger: `state_version` and `occurred_at`
// come off GET /ledger/markets/{id}/snapshot, and a unit test stands in for
// that route with MSW — so only this sees the ledger's own counter decide what
// a card says. The book's seed price is the same 50.0% whether nobody has
// traded it or the crowd has argued it back to the middle, and the counter is
// the only thing that tells the two apart.
//
// Both markets are published under one search term and the list is kept
// searched, so the two cards sit side by side on one page load however much
// earlier runs left in the database. That contrast is the bug: a grid of
// identical 50.0% read as an empty product rather than an open one.
test('a browse card tells a market nobody has traded from one that has been', async ({ page }) => {
  const admin = await registerAdmin('ut_admin')
  const suffix = uniqueName('untraded')
  const traded = await publishMarket(admin, `Will ${suffix} be traded?`)
  const untraded = await publishMarket(admin, `Will ${suffix} be left alone?`)

  const trader = await registerTrader('ut_trader')
  // Only the first market is touched; the second keeps state_version 0, which
  // the browser's own snapshot request is what opens a book for.
  await buy(trader, traded, 'Yes', '50.0000')

  await logIn(page, trader)
  await page.getByRole('searchbox', { name: /search markets/i }).fill(suffix)
  await page.keyboard.press('Enter')

  const tradedCard = marketCard(page, traded.question)
  // 50 Yes shares at b = 100 (support/backend.ts) give 0.6225.
  await expect(tradedCard.getByLabel('Yes price')).toHaveText('62.3%')
  // The exact wording and how it coarsens with age are pinned in the unit
  // tests; here the point is that the ledger's occurred_at reached the card at
  // all, so the unit is whatever the seconds since the trade make it.
  await expect(tradedCard.getByText(/last trade/i)).toBeVisible()

  const untradedCard = marketCard(page, untraded.question)
  await expect(untradedCard.getByText(/no trades yet/i)).toBeVisible()
  // No price at all on that one — not the seed 50.0%, and not the dash that
  // stands for a price nobody could read (#431).
  await expect(untradedCard.getByLabel('Yes price')).toHaveCount(0)
  await expect(untradedCard.getByText('50.0%')).toHaveCount(0)
  // And its outcomes are still named (#214).
  await expect(untradedCard.getByText('Yes', { exact: true })).toBeVisible()
})
