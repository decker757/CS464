import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { boxOf, logIn, marketCard } from './support/browser'

// [X-1] #450: a card showed what the crowd thinks and nothing about the
// trader, so a market they hold shares in looked exactly like one they had
// never touched.
//
// A unit test fakes the ledger with MSW, so only this sees the browse list and
// `GET /ledger/portfolio/me` agree on the outcome ids that put a quantity
// beside the right label — the same join `browse-prices.e2e.ts` exists to
// check for the prices.
test('a browse card shows what the trader holds, and says nothing about a market they do not', async ({ page }) => {
  const admin = await registerAdmin('bh_admin')
  // Not Yes and No, so the card cannot be showing labels of its own. Closing
  // within the hour, so soonest-first puts both on the first page however many
  // markets earlier runs left open ([X-1] #104 paged the list).
  const held = await publishMarket(
    admin,
    `Will ${uniqueName('bh_held')} be held?`,
    ['Lakers', 'Celtics'],
    { closesInHours: 1 },
  )
  const untouched = await publishMarket(
    admin,
    `Will ${uniqueName('bh_free')} be untouched?`,
    ['Lakers', 'Celtics'],
    { closesInHours: 1 },
  )
  const trader = await registerTrader('bh_trader')

  // Both sides of the one market, so the card has to name each (AC 4) — and
  // holding both is the ordinary case on a binary book.
  await buy(trader, held, 'Lakers', '40.0000')
  await buy(trader, held, 'Celtics', '12.0000')

  await logIn(page, trader)
  await page.goto('/markets')

  const heldCard = marketCard(page, held.question)
  await expect(heldCard.getByText('You hold')).toBeVisible()
  await expect(heldCard.getByText('40.0000 Lakers')).toBeVisible()
  await expect(heldCard.getByText('12.0000 Celtics')).toBeVisible()

  // The market they have not touched carries no empty slot and no dash
  // (AC 2) — and it is on screen, so this is the absence of a holding rather
  // than the absence of a card.
  //
  // Its outcome names are the proof the card rendered, not its prices: nobody
  // has traded it, and the batch snapshot opens no book, so it shows the
  // "no trades yet" invitation rather than a number (#445, ledger-service.md).
  const untouchedCard = marketCard(page, untouched.question)
  await expect(untouchedCard.getByText('Lakers')).toBeVisible()
  await expect(untouchedCard.getByText('You hold')).toHaveCount(0)
})

// An outcome label is the market author's text and may run to 120 characters
// (market_service's MAX_LABEL_LENGTH). The holding names it, and on a phone a
// pill held to one line would push the card, and the page, wider than the
// screen. jsdom lays nothing out, so only a browser can measure it.
test('a long held label wraps inside its card on a phone', async ({ page }) => {
  const admin = await registerAdmin('bh_long')
  const longLabel = 'Lakers by more than ten points after the fourth quarter overtime'
  const market = await publishMarket(
    admin,
    `Will ${uniqueName('bh_long')} fit a phone?`,
    [longLabel, 'Celtics'],
    { closesInHours: 1 },
  )
  const trader = await registerTrader('bh_long_trader')
  await buy(trader, market, longLabel, '40.0000')

  await page.setViewportSize({ width: 390, height: 844 })
  await logIn(page, trader)
  await page.goto('/markets')

  const card = marketCard(page, market.question)
  const holding = card.getByText(`40.0000 ${longLabel}`)
  await expect(holding).toBeVisible()
  const cardBox = await boxOf(card)
  const holdingBox = await boxOf(holding)
  expect(holdingBox.x + holdingBox.width).toBeLessThanOrEqual(cardBox.x + cardBox.width)
})
