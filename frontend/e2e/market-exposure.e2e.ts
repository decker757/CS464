import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { loadUntilShown, logIn } from './support/browser'

// [FE][2.2] #55, through the real ledger: a unit test fakes #6's response
// with MSW, so a field #6 renamed (as it did: exceeds_pool -> payout_exceeds_pool,
// max_payout -> payout_if_wins) would still pass there. Only the real
// endpoint catches that.
test('an admin sees shares outstanding for a traded outcome, with no flag', async ({ page }) => {
  const admin = await registerAdmin('exp_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('exposure')} resolve yes?`)
  const trader = await registerTrader('exp_trader')
  await buy(trader, market, 'Yes', '5')

  await logIn(page, admin)
  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('link', { name: 'Exposure' }).click()

  await expect(page.getByRole('heading', { name: market.question })).toBeVisible()
  // Shares and payout are both 5 here, so check each column on its own: a
  // row-wide "contains 5.0000" passes with the payout column broken.
  await expect(page.getByRole('columnheader')).toHaveText(['Outcome', 'Shares outstanding', 'Payout if wins', 'Pool residue if wins'])
  const yesCells = page.getByRole('row', { name: /yes/i }).getByRole('cell')
  const sharesCell = yesCells.nth(1)
  const payoutCell = yesCells.nth(2)
  await expect(sharesCell).toHaveText('5.0000')
  await expect(payoutCell).toHaveText('5.0000')
  expect(await page.getByRole('alert').count()).toBe(0)
})

test('a market nobody has traded shows no trades yet', async ({ page }) => {
  const admin = await registerAdmin('exp_empty_admin')
  // Log in before publishing. logIn lands on /markets, which fetches a price
  // snapshot for every market it lists, and the first snapshot opens the
  // market's book. A market published first would reach the exposure page
  // with a book and zero shares, not the "no book" state this test is about.
  await logIn(page, admin)
  const market = await publishMarket(admin, `Will ${uniqueName('untraded')} resolve yes?`)

  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('link', { name: 'Exposure' }).click()

  await expect(page.getByText(/no trades yet/i)).toBeVisible()
})
