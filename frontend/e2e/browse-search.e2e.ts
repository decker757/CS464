import { expect, test } from '@playwright/test'
import { closeEarly, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [FE][X-2] #35, through the real market service: GET /public/markets takes
// both q and status, so a unit test standing in for the server with MSW
// cannot catch a change to how either is read there. This does.
//
// Clearing the filters back to "All" is already covered by a unit test
// ('shows the active filters and clears them one at a time or all at once')
// and is not repeated here against the real list: this database accumulates
// markets across every run, and "All" is unscoped, so finding a market again
// there depends on paging through however many other runs left stopped more
// recently — not something worth pinning an end-to-end test to.
test('a trader searches the market list and narrows it by status', async ({ page }) => {
  const admin = await registerAdmin('bs_admin')
  const suffix = uniqueName('search')
  const first = await publishMarket(admin, `Will ${suffix} resolve yes?`)
  const second = await publishMarket(admin, `Is ${suffix} already decided?`)
  await closeEarly(admin, second.id)

  const trader = await registerTrader('bs_trader')
  await logIn(page, trader)
  await page.goto('/markets')

  await page.getByRole('searchbox', { name: /search markets/i }).fill(suffix)
  await page.keyboard.press('Enter')
  await expect(marketCard(page, first.question)).toBeVisible()
  await expect(marketCard(page, second.question)).toBeVisible()

  await page.getByRole('button', { name: 'Open' }).click()
  await expect(marketCard(page, first.question)).toBeVisible()
  await expect(marketCard(page, second.question)).toHaveCount(0)
})
