import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, startingGrant, suspend, uniqueName } from './support/backend'
import { logIn } from './support/browser'
import { creditsLess, formatPreciseCredits, formatSignedCredits } from './support/credits'

// [FE][4.1] #57, through the real auth service and ledger: the search is the
// auth service's own (GET /admin/users), the balance and history are the
// ledger's, by the id the search returns — exactly the join unit tests
// against MSW fakes can't catch.
test('an admin searches for a trader and reads their real balance and history', async ({ page }) => {
  const grant = await startingGrant()
  const admin = await registerAdmin('au_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('adminusers')} resolve yes?`)
  const trader = await registerTrader('au_trader')
  await buy(trader, market, 'Yes', '5.0000')

  await logIn(page, admin)
  await page.getByRole('link', { name: 'Users', exact: true }).click()

  // The list is newest first, so the trader is on top before any search. The
  // admin, registered a moment earlier, is the user the search has to drop.
  // Look only in the user cards: the navbar shows the logged-in admin's name
  // on every page.
  const userCards = page.getByRole('link').filter({ hasText: '@example.com' })
  const adminCard = userCards.getByText(admin.username, { exact: true })
  await expect(adminCard).toBeVisible()
  await page.getByLabel('Search by username or email').fill(trader.username)
  await page.getByRole('button', { name: 'Search' }).click()
  // One card once the filtered list arrives. While it loads there are none,
  // so the loading state cannot pass this by hiding the admin.
  await expect(userCards).toHaveCount(1)
  await expect(adminCard).toBeHidden()
  await page.getByText(trader.username, { exact: true }).click()

  await expect(page.getByRole('heading', { name: trader.username })).toBeVisible()
  // liquidity_b is 100 and nothing else traded against this market, so this
  // is exact, not just present — the one test that talks to the real ledger:
  // the grant less the 2.5313 the buy cost.
  const balance = formatPreciseCredits(creditsLess(grant, '2.5313'))
  await expect(page.getByText(`${balance} credits`, { exact: true })).toBeVisible()
  const rows = page.getByRole('row')
  await expect(rows.nth(1)).toContainText(market.question)
  // Each figure is matched as its own cell, not as part of the row's text,
  // where "-12.5313" would contain it.
  await expect(rows.nth(1).getByText('-2.5313', { exact: true })).toBeVisible()
  await expect(rows.nth(2)).toContainText('Starting grant')
  await expect(rows.nth(2).getByText(formatSignedCredits(grant), { exact: true })).toBeVisible()
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

  await expect(page.getByText('0.0000 credits', { exact: true })).toBeVisible()
  await expect(page.getByText(/no activity for this user yet/i)).toBeVisible()
})

// #479, through the real auth service: the unit tests fake the server, so they
// would all still pass if the page and the service spelled `role` or
// `is_suspended` differently. Each step here changes what is on screen, so a
// filter the server ignored or refused fails it.
test('an admin narrows the users list to suspended users, to admins, and to both', async ({ page }) => {
  const admin = await registerAdmin('auf_admin')
  const trader = await registerTrader('auf_trader')
  await suspend(admin, trader.id)

  await logIn(page, admin)
  await page.getByRole('link', { name: 'Users', exact: true }).click()

  // Look only in the user cards: the navbar shows the admin's name too.
  const userCards = page.getByRole('link').filter({ hasText: '@example.com' })
  const traderCard = userCards.getByText(trader.username, { exact: true })
  const searchBox = page.getByLabel('Search by username or email')
  const roles = page.getByRole('radiogroup', { name: 'Filter by role' })
  const statuses = page.getByRole('radiogroup', { name: 'Filter by status' })

  await searchBox.fill(trader.username)
  await page.getByRole('button', { name: 'Search' }).click()
  await expect(traderCard).toBeVisible()

  // The trader is suspended, so Active only leaves nobody and Suspended only
  // brings them back: both values of is_suspended reach the server.
  await statuses.getByRole('radio', { name: 'Active only' }).click()
  await expect(page.getByText('No users match that search with this filter.')).toBeVisible()
  await expect(traderCard).toBeHidden()
  await statuses.getByRole('radio', { name: 'Suspended only' }).click()
  await expect(traderCard).toBeVisible()

  // A trader is not an admin, so adding the role filter empties the list.
  await roles.getByRole('radio', { name: 'Admins only' }).click()
  await expect(page.getByText('No users match that search with these filters.')).toBeVisible()
  await expect(traderCard).toBeHidden()

  await statuses.getByRole('radio', { name: 'Any status' }).click()
  await searchBox.fill(admin.username)
  await page.getByRole('button', { name: 'Search' }).click()
  await expect(userCards.getByText(admin.username, { exact: true })).toBeVisible()
})
