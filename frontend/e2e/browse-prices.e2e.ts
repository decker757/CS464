import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [X-1] #126 and #214: the browse list comes from market_service, naming each
// outcome, and the prices from the ledger, one snapshot per card. A unit test
// fakes both with MSW, so only this sees the two agree on the outcome ids that
// put each price beside its label.
test('a browse card shows each outcome by name, with the price the ledger quotes for it', async ({ page }) => {
  const admin = await registerAdmin('bp_admin')
  // Not Yes and No, so the card cannot be showing labels of its own.
  const market = await publishMarket(admin, `Will ${uniqueName('browse')} be priced?`, ['Lakers', 'Celtics'])
  const trader = await registerTrader('bp_trader')

  // Moved off 50/50 first, so the card cannot be showing an opening price.
  // 50 Lakers shares at b = 100 (support/backend.ts) give 0.6225 and 0.3775.
  await buy(trader, market, 'Lakers', '50.0000')
  const { prices } = await snapshot(trader, market.id)
  const priceOf = (label: string) => {
    const outcome = market.outcomes.find(candidate => candidate.label === label)
    return prices.find(candidate => candidate.outcome_id === outcome?.id)?.price
  }
  expect(priceOf('Lakers')).toBe('0.6225')
  expect(priceOf('Celtics')).toBe('0.3775')

  await logIn(page, trader)
  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Lakers price')).toHaveText('62.3%')
  await expect(card.getByLabel('Celtics price')).toHaveText('37.8%')
})
