import { expect, test } from './support/fixtures'
import { registerTrader, startingGrant } from './support/backend'
import { logIn } from './support/browser'
import { formatWholeCredits } from './support/credits'

// [B-2] #33, through the real ledger: GET /ledger/balances/me mints the
// starting grant on its first read and the navbar's own call to it, not an
// MSW fake — if either side renamed a field, a unit test would not catch it.
test("a freshly registered trader's starting balance shows in the navbar", async ({ page }) => {
  const grant = await startingGrant()
  const trader = await registerTrader('balance')

  await logIn(page, trader)

  await expect(page.getByLabel('available balance')).toHaveText(`${formatWholeCredits(grant)} credits`)
})
