import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// [T-5] #495's Definition of Done: two traders on one market; when one buys,
// the other's chart gains a point without a reload.
//
// No unit test can see this. MSW fakes both the history route and the socket,
// so the only frame it can observe is one it published itself. Here the trade
// happens outside the browser entirely — nothing is clicked, nothing is
// reloaded — so the whole path has to work: the ledger's commit, the Redis
// publish, the relay's fan-out, and the chart folding the frame into the
// history it already drew.
test("a watcher's price chart gains a point when another trader buys", async ({ page }) => {
  const admin = await registerAdmin('clp_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('chart')} move the chart?`)
  const watcher = await registerTrader('clp_watch')
  const buyer = await registerTrader('clp_buy')

  // Two trades before the page opens, so the chart has a line to extend
  // rather than the "no trades yet" message: the opening point plus these
  // make three, and the section needs two to draw at all.
  await buy(buyer, market, 'Yes', '10.0000')
  await buy(buyer, market, 'Yes', '10.0000')

  await logIn(page, watcher)

  // Every frame this page's socket receives, recorded from before it opens.
  const frameTypes: string[] = []
  page.on('websocket', socket => {
    socket.on('framereceived', frame => frameTypes.push(JSON.parse(String(frame.payload)).type))
  })
  await page.goto(`/markets/${market.id}`)

  // Three points: the book's opening and the two trades above.
  await expect(page.getByText(/showing all 3 price points/i)).toBeVisible()
  await expect.poll(() => frameTypes).toContain('subscribed')

  // Somebody else's trade, through the API rather than the panel: this test is
  // about the watcher's chart receiving it, not about placing it.
  await buy(buyer, market, 'Yes', '10.0000')

  // The frame arrived on the socket — a refetch would show the same number
  // and prove nothing about the live path, which is why this polls the frames
  // as well as the label.
  await expect.poll(() => frameTypes).toContain('price')
  await expect(page.getByText(/showing all 4 price points/i)).toBeVisible()
})
