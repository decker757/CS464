import { expect, test } from './support/fixtures'
import { registerTrader } from './support/backend'
import { logIn, logOut } from './support/browser'

// [FE] #437, in a real browser. The row spans two services — the balance is
// the ledger's and the session is auth's — and the two things a unit test
// cannot see are whether the menu really opens over a real page and whether
// the Log Out inside it really ends the session.
test('a trader sees the balance without a click, and signs out from the account menu', async ({ page }) => {
  const trader = await registerTrader('cluster')

  await logIn(page, trader)

  // The figure a trader checks most often, on screen with nothing to open.
  // A figure and its unit, not a particular total: what the starting grant is
  // worth is `balance.e2e.ts`'s question, and STARTING_CREDITS is a setting.
  await expect(page.getByLabel('available balance')).toHaveText(/^[\d,]+ credits$/)
  // And not offered as somewhere to go: it used to carry the active
  // navigation link's exact styling.
  await expect(page.getByRole('banner').getByRole('link', { name: /credits/ })).toHaveCount(0)
  // Sign-out is one click away rather than sitting beside the navigation.
  await expect(page.getByRole('button', { name: 'Log Out' })).toHaveCount(0)

  await logOut(page, trader)

  await expect(page.getByText(trader.username, { exact: true })).toHaveCount(0)
})
