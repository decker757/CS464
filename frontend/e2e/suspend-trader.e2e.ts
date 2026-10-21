import { expect, test } from './support/fixtures'
import { PASSWORD, registerAdmin, registerTrader } from './support/backend'
import { logIn, logOut } from './support/browser'

// [FE][4.2] #60, through the real auth service: an admin's own view of
// is_suspended is fed through MSW everywhere else, so this is the one seam
// that proves a real suspend call really stops the account logging in
// (auth-service.md: "the account cannot log in: 403 account_suspended").
test('an admin suspends a trader through the UI, and that trader can no longer log in', async ({ page }) => {
  const admin = await registerAdmin('sus_admin')
  const trader = await registerTrader('sus_trader')

  await logIn(page, admin)
  await page.getByRole('link', { name: 'Users', exact: true }).click()
  await page.getByLabel('Search by username or email').fill(trader.username)
  await page.getByRole('button', { name: 'Search' }).click()
  await page.getByText(trader.username, { exact: true }).click()

  await expect(page.getByRole('heading', { name: trader.username })).toBeVisible()
  await page.getByRole('button', { name: /^suspend$/i }).click()
  await page.getByLabel(/reason/i).fill('Typed by the end-to-end test.')
  await page.getByRole('button', { name: /confirm suspend/i }).click()

  await expect(page.getByText('Suspended')).toBeVisible()

  await logOut(page, admin)
  await page.getByLabel('Username or Email').fill(trader.username)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: /log in/i }).click()

  // The server's own message for 403 account_suspended, not just any alert:
  // a wrong password or a downed service would also leave one on screen.
  await expect(page.getByText('This account is suspended. Contact an administrator.')).toBeVisible()
})
