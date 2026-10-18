import { expect, test } from './support/fixtures'
import { PASSWORD, registerTrader } from './support/backend'
import { logIn } from './support/browser'

// [FE] #353, through the real auth service. Clearing the cookie jar is what an
// expired access token and an expired refresh token look like to the next
// request the open tab sends: a 401 `invalid_token`, a /auth/refresh the
// server refuses, and nothing left to sign the next one with. A unit test
// fakes that chain with MSW; only a browser holds the cookies that make it.
test('an expired session goes to the login page and comes back to where it was', async ({ page }) => {
  const trader = await registerTrader('expiry')
  await logIn(page, trader)

  // History first, and not because the page matters: the markets list holds a
  // price socket per market and refetches a snapshot from the ledger whenever
  // one reconnects, so a session cleared while sitting there expires on
  // whichever request happens to be in the air. This page asks once and then
  // waits, which leaves the click below as the only thing that can trigger it.
  await page.getByRole('link', { name: 'History' }).click()
  // The signup grant, the one entry a trader who has never traded has
  // (ADR 0009). Waiting for it is waiting for this page's only request.
  await expect(page.getByText('Starting grant')).toBeVisible()

  // The session ends while the tab is open. Nothing reloads, so the app still
  // believes it is signed in — which is the whole bug.
  await page.context().clearCookies()

  await page.getByRole('link', { name: 'Portfolio' }).click()

  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByText('Your session expired, please sign in again.')).toBeVisible()
  // The navbar used to keep the username and a stale balance on screen.
  await expect(page.getByText(trader.username, { exact: true })).toHaveCount(0)
  await expect(page.getByLabel('available balance')).toHaveCount(0)

  // /portfolio, not /markets: signing in again returns to the page the expiry
  // interrupted, which is what ProtectedRoute's `from` is carried for.
  await page.getByLabel('Username or Email').fill(trader.username)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: /log in/i }).click()

  await expect(page).toHaveURL(/\/portfolio$/)
})
