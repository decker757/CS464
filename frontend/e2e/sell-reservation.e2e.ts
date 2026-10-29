import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// #410's frontend half (#494): the sell control offers
// `quantity - reserved_shares`, and both come from `GET /ledger/portfolio/me`.
//
// A unit test fakes the portfolio, so only this run checks that the ledger's
// real `reserved_shares` reaches the panel: a resting sell lowers what it
// offers, and a cancel gives it back. It cannot tell that figure from the
// browser adding up the orders list itself — both come to 30 here — so the
// unit tests are what pin which of the two the panel reads.
test('a resting sell order lowers what the panel offers to sell, and cancelling gives it back', async ({ page }) => {
  const admin = await registerAdmin('sr_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('reserve')} be reserved?`)
  const trader = await registerTrader('sr_trader')

  // Shares to sell, bought through the API: this test is about the sell
  // clamp, not about buying (trading-panel.e2e.ts covers the panel's buy).
  await buy(trader, market, 'Yes', '50.0000')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  // The whole position is sellable before any order holds part of it back.
  await page.getByRole('button', { name: /^sell$/i }).click()
  // The labelled element carries the Max button too, so these match rather
  // than equal its whole text.
  const sharesHeld = page.getByLabel('shares held')
  await expect(sharesHeld).toContainText('You hold 50.0000 shares')

  // A sell well above the current price rests instead of filling, holding
  // back the shares it would sell.
  await page.getByRole('button', { name: 'Limit', exact: true }).click()
  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  await page.getByRole('button', { name: /^sell$/i }).click()
  await page.getByLabel('Limit price').fill('0.95')
  await page.getByLabel('Quantity (shares)').fill('20')
  await page.getByRole('button', { name: 'Place sell order', exact: true }).click()

  await expect(page.getByText('Sell 20.0000 Yes at 0.9500: resting until the price reaches it.', { exact: true })).toBeVisible()

  // The panel now offers the rest, and still names the whole position: a
  // trader whose shares are held back has not stopped holding them
  // (#451 / #478).
  await page.getByRole('button', { name: 'Market', exact: true }).click()
  await page.getByRole('button', { name: /^sell$/i }).click()
  await expect(sharesHeld).toContainText('You can sell 30.0000 of your 50.0000 shares')

  // Max takes the figure the panel offers, not the whole position — a sell of
  // the lot is what the ledger would refuse.
  await page.getByRole('button', { name: 'Max', exact: true }).click()
  await expect(page.getByLabel('Quantity (shares)')).toHaveValue('30.0000')

  // Cancelling the order releases the shares, and the clamp goes with it.
  await page.getByRole('button', { name: 'Cancel sell Yes at 0.9500', exact: true }).click()

  await expect(sharesHeld).toContainText('You hold 50.0000 shares')
  await page.getByRole('button', { name: 'Max', exact: true }).click()
  await expect(page.getByLabel('Quantity (shares)')).toHaveValue('50.0000')
})
