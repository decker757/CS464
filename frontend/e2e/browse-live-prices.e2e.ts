import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName, type Market } from './support/backend'
import { chipColours, logIn, marketCard } from './support/browser'
import { AA_SMALL_TEXT, contrastRatio } from '../src/test/contrast'

/** What the ledger quotes for one outcome of `market`, by its id. */
function priceOf(market: Market, prices: { outcome_id: string; price: string }[], label: string): string | undefined {
  const outcome = market.outcomes.find(candidate => candidate.label === label)
  return prices.find(candidate => candidate.outcome_id === outcome?.id)?.price
}

// [X-1] #444's Definition of Done: "a trade in one context moves the price on
// a browse list open in another".
//
// No unit test can see this. MSW fakes both services, so the only frame it can
// observe is one it published itself, and the subscribe the browse page sends
// is answered by a stand-in rather than by the relay. Here the trade happens
// outside the browser entirely: nothing is clicked, nothing is reloaded, and
// the whole path — ledger commit, Redis publish, relay fan-out, the socket's
// cookie auth and origin check, the card's repaint — has to work.
//
// The buyer acts through the backend helper rather than the trading panel on
// purpose: this is about the list receiving the update, not about placing the
// trade. trading-panel.e2e.ts covers the panel.
test('a browse card moves when someone else buys, without a reload', async ({ page }) => {
  const admin = await registerAdmin('blp_admin')
  // Not Yes and No, so the card cannot be showing labels of its own. Closing
  // within the hour, so soonest-first puts it on the first page however many
  // markets earlier runs left open ([X-1] #104 paged the list).
  const market = await publishMarket(
    admin,
    `Will ${uniqueName('blp')} move live?`,
    ['Lakers', 'Celtics'],
    { closesInHours: 1 },
  )
  const watcher = await registerTrader('blp_watch')
  const buyer = await registerTrader('blp_buy')

  // Moved off 50/50 before the page opens, so the first paint cannot be an
  // opening price and the move below is unambiguous.
  await buy(buyer, market, 'Lakers', '50.0000')

  await logIn(page, watcher)

  // Every frame this page's socket receives, recorded from before it opens.
  const frameTypes: string[] = []
  page.on('websocket', socket => {
    socket.on('framereceived', frame => frameTypes.push(JSON.parse(String(frame.payload)).type))
  })
  await page.goto('/markets')

  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Lakers price')).toHaveText('62.3%')
  // The browse list subscribes the cards it renders, not just the one a
  // trader opened: this is the ack for a market on a *list*.
  await expect.poll(() => frameTypes).toContain('subscribed')

  // Another 50 shares through the API: q = 100 at b = 100 gives 0.7311.
  await buy(buyer, market, 'Lakers', '50.0000')
  const { prices } = await snapshot(buyer, market.id)
  expect(priceOf(market, prices, 'Lakers')).toBe('0.7311')

  // The price arrived on the socket, and not on the thirty-second poll. The
  // timeout is the proof: a list that waited for its next read would still be
  // showing 62.3% here, which is what this ticket exists to fix.
  await expect.poll(() => frameTypes).toContain('price')

  // The card says the price moved and says which way, and the accessible name
  // is the half of that cue which is not colour (AC 3). Asserted by the same
  // locator that proves the new price is on screen, because the cue is
  // deliberately brief — a separate wait for the number first would expire it.
  // The arrow is part of the text, so this matches rather than equals it.
  await expect(card.getByLabel('Lakers price rose')).toContainText('73.1%', { timeout: 10_000 })

  // And the cue is temporary: the price stays, the movement mark does not.
  await expect(card.getByLabel('Lakers price')).toHaveText('73.1%')
})

// [FE] #574, in the real browser: while a chip is tinted for a move, its label
// still reads at AA. index.css.test.ts measures the token pair; only this sees
// the chip actually use it, and the tint Tailwind actually paints. Three
// outcomes, because a two-outcome card draws a split bar with no chips.
test("a browse chip's label stays AA on the tint that marks its price as moved", async ({ page }) => {
  const admin = await registerAdmin('bct_admin')
  const market = await publishMarket(
    admin,
    `Will ${uniqueName('bct')} move live?`,
    ['Red', 'Green', 'Blue'],
    { closesInHours: 1 },
  )
  const watcher = await registerTrader('bct_watch')
  const buyer = await registerTrader('bct_buy')
  // Traded once before the page opens: an untraded card shows no prices.
  await buy(buyer, market, 'Red', '50.0000')

  // No fade, so the tint is fully there the moment the cue is.
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await logIn(page, watcher)
  await page.goto('/markets')
  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Red price')).toBeVisible()

  // One buy of Red: Red rises onto the green tint, and Green and Blue fall
  // onto the red one, so both tints are on screen at once.
  await buy(buyer, market, 'Red', '50.0000')
  await expect(card.getByLabel('Red price rose')).toBeVisible({ timeout: 10_000 })
  await expect(card.getByLabel('Green price fell')).toBeVisible()
  const rose = await chipColours(card, 'Red')
  const fell = await chipColours(card, 'Green')

  expect(contrastRatio(rose.text, rose.background)).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  expect(contrastRatio(fell.text, fell.background)).toBeGreaterThanOrEqual(AA_SMALL_TEXT)

  // Both reads were of a tint: once the cue is over, each chip is back on a
  // different colour. Otherwise a read that came too late would pass on the
  // resting chip, which index.css.test.ts already measures.
  await expect(card.getByLabel('Red price', { exact: true })).toBeVisible()
  await expect(card.getByLabel('Green price', { exact: true })).toBeVisible()
  expect((await chipColours(card, 'Red')).background).not.toEqual(rose.background)
  expect((await chipColours(card, 'Green')).background).not.toEqual(fell.background)
})
