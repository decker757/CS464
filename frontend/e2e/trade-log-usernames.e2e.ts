import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { loadUntilShown, logIn } from './support/browser'

// [FE][2.4] #434, across two services in one browser. The trade log's rows
// come from the ledger, which knows a trader only by the id in a signed token
// (ADR 0003); the name comes from auth's own `POST /admin/users/lookup`. A
// unit test fakes both with MSW, so an id joined on the wrong field — or a
// lookup never called at all, which is what this ticket was — still passes
// there. This is the one test where the two real services have to agree.
test('an admin reads traders by name on the trade log and clicks through to one history', async ({ page }) => {
  const admin = await registerAdmin('tlu_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('tradelog')} resolve yes?`)
  const trader = await registerTrader('tlu_trader')
  await buy(trader, market, 'Yes', '5')

  await logIn(page, admin)
  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('link', { name: 'Price History' }).click()

  // Newest first on the trade log; row 0 is the header. The User column is
  // the trader's name, where it used to be eight characters of their uuid.
  const newestTrade = page.getByRole('row').nth(1)
  const trade = newestTrade.getByRole('link', { name: trader.username })
  await expect(trade).toBeVisible()

  await trade.click()

  // Clicking from here carries no username in router state, the way the user
  // list's own link does, so this heading is what the page looked up for
  // itself — and the id is still on screen to quote.
  await expect(page).toHaveURL(new RegExp(`/admin/users/${trader.id}$`))
  await expect(page.getByRole('heading', { name: `${trader.username}'s History` })).toBeVisible()
  await expect(page.getByText(trader.id)).toBeVisible()
})
