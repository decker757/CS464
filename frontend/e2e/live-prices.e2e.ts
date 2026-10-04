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

  // Every frame the page's socket receives, recorded from before it opens.
  const frameTypes: string[] = []
  page.on('websocket', socket => {
    socket.on('framereceived', frame => frameTypes.push(JSON.parse(String(frame.payload)).type))
  })
  await page.goto(`/markets/${market.id}`)
  await expect.poll(() => frameTypes).toContain('subscribed')
  const yesPrice = page.getByLabel('Yes price')
  await expect(yesPrice).toHaveText('50.0%')

  await buy(buyer, market, 'Yes', '50.0000')

  // The page also fetches a snapshot over HTTP when it subscribes, so the new
  // price on screen alone does not prove the socket delivered it. This does.
  await expect.poll(() => frameTypes).toContain('price')
  const after = await snapshot(buyer, market.id)
  const yes = market.outcomes.find(outcome => outcome.label === 'Yes')
  const expected = after.prices.find(price => price.outcome_id === yes?.id)
  // 50 Yes shares at b = 100 (support/backend.ts) move Yes to 0.6225.
  expect(expected?.price).toBe('0.6225')
  await expect(yesPrice).toHaveText('62.3%')
})
