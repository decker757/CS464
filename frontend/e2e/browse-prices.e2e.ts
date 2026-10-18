import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName, type Market } from './support/backend'
import { logIn, marketCard } from './support/browser'

/** What the ledger quotes for one outcome of `market`, by its id. */
function priceOf(market: Market, prices: { outcome_id: string; price: string }[], label: string): string | undefined {
  const outcome = market.outcomes.find(candidate => candidate.label === label)
  return prices.find(candidate => candidate.outcome_id === outcome?.id)?.price
}

// [X-1] #126 and #214: the browse list comes from market_service, naming each
// outcome, and the prices from the ledger, one snapshot per card. A unit test
// fakes both with MSW, so only this sees the two agree on the outcome ids that
// put each price beside its label.
test('a browse card shows each outcome by name, with the price the ledger quotes for it', async ({ page }) => {
  const admin = await registerAdmin('bp_admin')
  // Not Yes and No, so the card cannot be showing labels of its own.
  // Closing within the hour, so soonest-first puts it on the first page however
  // many markets earlier runs left open ([X-1] #104 paged the list).
  const market = await publishMarket(admin, `Will ${uniqueName('browse')} be priced?`, ['Lakers', 'Celtics'], { closesInHours: 1 })
  const trader = await registerTrader('bp_trader')

  // Moved off 50/50 first, so the card cannot be showing an opening price.
  // 50 Lakers shares at b = 100 (support/backend.ts) give 0.6225 and 0.3775.
  await buy(trader, market, 'Lakers', '50.0000')
  const { prices } = await snapshot(trader, market.id)
  expect(priceOf(market, prices, 'Lakers')).toBe('0.6225')
  expect(priceOf(market, prices, 'Celtics')).toBe('0.3775')

  await logIn(page, trader)
  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Lakers price')).toHaveText('62.3%')
  await expect(card.getByLabel('Celtics price')).toHaveText('37.8%')
})

// [X-1] #364 AC 1: the list re-reads the prices by itself. No unit test can
// see this — MSW fakes the ledger, so the only refresh it can observe is one
// it served itself. Here the trade happens outside the browser and nothing is
// clicked or reloaded: the card has to notice on its own.
test('a browse card picks up a new price without a reload', async ({ page }) => {
  // usePricesForMarkets re-reads every thirty seconds, so this test waits
  // where the others do not.
  test.setTimeout(120_000)
  const admin = await registerAdmin('bp_refresh_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('refresh')} be repriced?`, ['Lakers', 'Celtics'], { closesInHours: 1 })
  const trader = await registerTrader('bp_refresh_trader')

  await buy(trader, market, 'Lakers', '50.0000')
  await logIn(page, trader)
  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Lakers price')).toHaveText('62.3%')

  // Another 50 shares through the API: q = 100 at b = 100 gives 0.7311.
  await buy(trader, market, 'Lakers', '50.0000')
  const { prices } = await snapshot(trader, market.id)
  expect(priceOf(market, prices, 'Lakers')).toBe('0.7311')

  await expect(card.getByLabel('Lakers price')).toHaveText('73.1%', { timeout: 40_000 })
})
