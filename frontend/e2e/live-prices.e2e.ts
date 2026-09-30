import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// [X-3] #36 and [F-2] #42: a trade on the ledger reaches an open page through
// Redis and the realtime socket, with the socket's cookie auth and origin
// check in the way. Buying through the browser waits on #49's trading panel.
test("a trader's market page moves when someone else buys", async ({ page }) => {
  const admin = await registerAdmin('lp_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('live')} move?`)
  const watcher = await registerTrader('lp_watch')
  const buyer = await registerTrader('lp_buy')
  await logIn(page, watcher)

  const socketOpened = page.waitForEvent('websocket')
  await page.goto(`/markets/${market.id}`)
  const socket = await socketOpened
  // Subscribed before the trade, so the new price can only arrive as a frame.
  await socket.waitForEvent('framereceived', { predicate: frame => String(frame.payload).includes('"subscribed"') })
  const yesPrice = page.getByLabel('Yes price')
  await expect(yesPrice).toHaveText('50.0%')

  await buy(buyer, market, 'Yes', '50.0000')

  const after = await snapshot(buyer, market.id)
  const yes = market.outcomes.find(outcome => outcome.label === 'Yes')
  const expected = after.prices.find(price => price.outcome_id === yes?.id)
  await expect(yesPrice).toHaveText(`${(Number(expected?.price) * 100).toFixed(1)}%`)
  await expect(yesPrice).not.toHaveText('50.0%')
})
