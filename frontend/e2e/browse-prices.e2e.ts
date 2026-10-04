import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [X-1] #126: the browse list comes from market_service and the prices from
// the ledger, one snapshot per card. A unit test fakes both with MSW, so only
// this sees the two agree on a market id and a price shape.
test('a browse card shows the price the ledger quotes for that market', async ({ page }) => {
  const admin = await registerAdmin('bp_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('browse')} be priced?`)
  const trader = await registerTrader('bp_trader')

  // Moved off 50/50 first, so the card cannot be showing an opening price.
  // 50 Yes shares at b = 100 (support/backend.ts) give Yes 0.6225, No 0.3775.
  await buy(trader, market, 'Yes', '50.0000')
  const { prices } = await snapshot(trader, market.id)
  const priceOf = (label: string) => {
    const outcome = market.outcomes.find(candidate => candidate.label === label)
    return prices.find(candidate => candidate.outcome_id === outcome?.id)?.price
  }
  expect(priceOf('Yes')).toBe('0.6225')
  expect(priceOf('No')).toBe('0.3775')

  await logIn(page, trader)
  const card = marketCard(page, market.question)
  await expect(card.getByLabel('Yes price')).toHaveText('62.3%')
  await expect(card.getByLabel('No price')).toHaveText('37.8%')
})
