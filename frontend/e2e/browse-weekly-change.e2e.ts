import { expect, test } from './support/fixtures'
import { buy, LEDGER_URL, pricesWeekAgo, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// The batch snapshot route: the one request a browse page reads every card's
// prices through ([X-1] #432).
function isBatchSnapshotRead(url: string, method: string): boolean {
  return method === 'POST' && url === `${LEDGER_URL}/ledger/markets/snapshots`
}

// [X-1] #564: a browse card shows each outcome's change over the week, from
// the batch snapshot's `prices_7d_ago` (#446), and the page still reads every
// card's prices in one request rather than one per card.
//
// What no test here can show is a change on screen. The ledger measures from
// the price after a market's last trade at least seven days old, and a trade's
// time is the `created_at` of its rows in `ledger.entries`, which an
// append-only trigger keeps from being rewritten (ledger-service.md). A market
// this run creates has no trade that old and cannot be given one honestly, so
// the change itself is drawn in MarketCard.test and MarketsPage.test, and this
// pins the seam they cannot see: the real ledger's null for a market traded a
// moment ago, and a card that draws nothing for it — not a zero, and not a
// change from its seed price.
test('a market traded moments ago shows no change over the week, and the page reads its prices in one request', async ({ page }) => {
  const admin = await registerAdmin('wc_admin')
  // Closing within the hour so soonest-first puts it on the first page however
  // many markets earlier runs left open ([X-1] #104 paged the list).
  const market = await publishMarket(admin, `Will ${uniqueName('weekly')} move this week?`, ['Lakers', 'Celtics'], { closesInHours: 1 })
  const trader = await registerTrader('wc_trader')
  // Off its seed price, so a card that measured from the seed would draw a
  // change of about twelve points here.
  await buy(trader, market, 'Lakers', '50.0000')
  // The ledger's own answer: no week-old price for a market whose only trade
  // is seconds old. If that ever changes, the card's assertions below stop
  // proving anything, so it is checked rather than assumed.
  expect(await pricesWeekAgo(trader, market.id)).toBeNull()
  await logIn(page, trader)

  // Counted from before the load it is about, not from the login's own.
  const batchReads: string[] = []
  page.on('request', request => {
    if (isBatchSnapshotRead(request.url(), request.method())) batchReads.push(request.url())
  })
  // The relay's ack for this market, which is when the price socket reads the
  // ledger again if it is going to (usePriceSocket's `onAck`).
  let isAcked = false
  page.on('websocket', socket => {
    socket.on('framereceived', frame => {
      const message = JSON.parse(String(frame.payload)) as { type: string; market_id?: string }
      if (message.type === 'subscribed' && message.market_id === market.id) isAcked = true
    })
  })
  await page.reload()

  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Lakers price')).toHaveText('62.3%')
  // AC 2: nothing drawn for it at all.
  await expect(card.getByLabel(/over the last 7 days/)).toHaveCount(0)
  await expect(card.getByText(/pts|No change/)).toHaveCount(0)

  // AC 3: one batch read for the page. Counted once the socket has acked this
  // market, plus a second's grace: what this rules out is a request that was
  // never going to be waited on — a read the ack sends, or one per card as the
  // cards render — and both go out within moments of the ack. The next read
  // after those is the thirty-second poll (PRICE_REFRESH_MS), well clear.
  await expect.poll(() => isAcked).toBe(true)
  await page.waitForTimeout(1_000)
  expect(batchReads).toHaveLength(1)
})
