import { expect, test } from './support/fixtures'
import { buy, LEDGER_URL, pricesWeekAgo, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName } from './support/backend'
import { logIn, marketCard, setPricesWeekAgo } from './support/browser'

// The batch snapshot route: the one request a browse page reads every card's
// prices through ([X-1] #432).
function isBatchSnapshotRead(url: string, method: string): boolean {
  return method === 'POST' && url === `${LEDGER_URL}/ledger/markets/snapshots`
}

// [X-1] #564: a browse card shows each outcome's change over the week, from
// the batch snapshot's `prices_7d_ago` (#446), and the page still reads every
// card's prices through the batch rather than once per card.

// The Definition of Done's journey: trade a market, then see its change on
// the card. A market this run creates has no trade a week old, so the batch
// field is handed a price the same market really had before its second trade
// (setPricesWeekAgo says why that is the only honest way). The rest is real:
// the trades, the batch request and every other field in its answer.
test('a traded market shows each outcome\'s change on its card', async ({ page }) => {
  const admin = await registerAdmin('wc_change_admin')
  // Closing within the hour so soonest-first puts it on the first page however
  // many markets earlier runs left open ([X-1] #104 paged the list).
  const market = await publishMarket(admin, `Will ${uniqueName('weekly_up')} move this week?`, ['Lakers', 'Celtics'], { closesInHours: 1 })
  const trader = await registerTrader('wc_change_trader')

  // 50 Lakers shares at b = 100 give 0.6225; 50 more give 0.7311.
  await buy(trader, market, 'Lakers', '50.0000')
  const earlier = await snapshot(trader, market.id)
  await buy(trader, market, 'Lakers', '50.0000')
  const now = await snapshot(trader, market.id)
  expect(earlier.prices.map(price => price.price)).toEqual(['0.6225', '0.3775'])
  expect(now.prices.map(price => price.price)).toEqual(['0.7311', '0.2689'])

  await setPricesWeekAgo(page, market.id, earlier.prices)
  await logIn(page, trader)

  // 0.7311 - 0.6225 is 10.86 points, each way.
  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Lakers up 10.9 points over the last 7 days')).toHaveText('▲ +10.9 pts')
  await expect(card.getByLabel('Celtics down 10.9 points over the last 7 days')).toHaveText('▼ -10.9 pts')
})

// The half nothing is handed: the real ledger's null for a market traded a
// moment ago, and a card that draws nothing for it — not a zero, and not a
// change from its seed price. And the warning #446 carried: a per-card fetch
// is exactly how this feature would regress.
test('a market traded moments ago shows no change, and its card makes no read of its own', async ({ page }) => {
  const admin = await registerAdmin('wc_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('weekly')} move this week?`, ['Lakers', 'Celtics'], { closesInHours: 1 })
  const trader = await registerTrader('wc_trader')
  // Off its seed price, so a card that measured from the seed would draw a
  // change of about twelve points here.
  await buy(trader, market, 'Lakers', '50.0000')
  // The ledger's own answer: no week-old price for a market whose only trade
  // is seconds old. If that ever changes, the card's assertions below stop
  // proving anything, so it is checked rather than assumed.
  expect(await pricesWeekAgo(trader, market.id)).toBeNull()

  // The login lands on the list, which sends its own batch read. Counting
  // starts only once that has answered, or it is counted against the reload.
  const loginBatch = page.waitForResponse(response => isBatchSnapshotRead(response.url(), response.request().method()))
  await logIn(page, trader)
  await loginBatch

  const batchReads: string[] = []
  const perMarketReads: string[] = []
  page.on('request', request => {
    const { pathname } = new URL(request.url())
    if (isBatchSnapshotRead(request.url(), request.method())) batchReads.push(pathname)
    // Price history is never read by the list. A single snapshot is, for a
    // market the batch left out because nobody has opened its book — which
    // another test's untouched market can be — but never for this one, which
    // has been traded and so is always in the batch.
    else if (pathname.endsWith('/price-history') || pathname === `/ledger/markets/${market.id}/snapshot`) perMarketReads.push(pathname)
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

  // AC 3. Counted once the socket has acked this market, plus a second's
  // grace: every read in question goes out within moments of the ack, and the
  // next one after that is the thirty-second poll (PRICE_REFRESH_MS).
  //
  // Up to two batch reads, not one. The page reads on mount, and the socket's
  // `subscribed` ack reads again by design — frames published between the
  // first read and the ack never reached the socket, so the ack's read is the
  // recovery path (usePricesForMarkets.test.ts). Whether it goes out depends
  // on whether the ack lands before the first read has answered. What must
  // never happen is a read per card, which is what the second list counts.
  await expect.poll(() => isAcked).toBe(true)
  await page.waitForTimeout(1_000)
  expect(batchReads.length).toBeGreaterThanOrEqual(1)
  expect(batchReads.length).toBeLessThanOrEqual(2)
  expect(perMarketReads).toEqual([])
})
