import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// [FE][4.1] #57, through the real auth service and ledger: the search is the
// auth service's own (GET /admin/users), the balance and history are the
// ledger's, by the id the search returns — exactly the join unit tests
// against MSW fakes can't catch.
test('an admin searches for a trader and reads their real balance and history', async ({ page }) => {
  const admin = await registerAdmin('au_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('adminusers')} resolve yes?`)
  const trader = await registerTrader('au_trader')
  await buy(trader, market, 'Yes', '5.0000')

  await logIn(page, admin)
  await page.getByRole('link', { name: 'Users', exact: true }).click()

  await page.getByLabel('Search by username or email').fill(trader.username)
  await page.getByRole('button', { name: 'Search' }).click()
  await page.getByText(trader.username, { exact: true }).click()

  // liquidity_b is 100 and nothing else traded against this market, so this
  // is exact, not just present — the one test that talks to the real ledger.
  await expect(page.getByText('997.4687 credits')).toBeVisible()
  const rows = page.getByRole('row')
  await expect(rows.nth(1)).toContainText(market.question)
  await expect(rows.nth(1)).toContainText('-2.5313')
  await expect(rows.nth(2)).toContainText('Starting grant')
})

test('a trader who has never read their own balance shows zero, not an error', async ({ page }) => {
  // ADR 0009 / #188: only the user's own /me read mints the starting grant.
  // An administrator's read of the same id must not see it either.
  const admin = await registerAdmin('au_admin2')
  const neverRead = await registerTrader('au_never')

  await logIn(page, admin)
  await page.getByRole('link', { name: 'Users', exact: true }).click()
  await page.getByLabel('Search by username or email').fill(neverRead.username)
  await page.getByRole('button', { name: 'Search' }).click()
  await page.getByText(neverRead.username, { exact: true }).click()

  await expect(page.getByText('0.0000 credits')).toBeVisible()
  await expect(page.getByText(/no activity for this user yet/i)).toBeVisible()
})
