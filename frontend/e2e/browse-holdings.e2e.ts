import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

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
