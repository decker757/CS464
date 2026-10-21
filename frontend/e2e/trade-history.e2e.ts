import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, startingGrant, uniqueName } from './support/backend'
import { logIn } from './support/browser'
import { creditsLess, formatPreciseCredits, formatSignedCredits } from './support/credits'

// [FE][T-5] #53, through the real ledger and market_service: the history
// route carries ids only, and the page's own join against
// GET /public/markets/{id} is exactly what unit tests against MSW fakes
// cannot catch.
test('a trader sees their starting grant and a trade, newest first, with the market joined in', async ({ page }) => {
  const grant = await startingGrant()
  const admin = await registerAdmin('th_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('history')} resolve yes?`)
  const trader = await registerTrader('th_trader')
  await buy(trader, market, 'Yes', '5.0000')

  await logIn(page, trader)
  // Through the navbar link, not page.goto, so this also proves the link works.
  await page.getByRole('link', { name: 'History', exact: true }).click()

  const rows = page.getByRole('row')
  // Newest first: the buy is above the grant.
  await expect(rows.nth(1)).toContainText(market.question)
  await expect(rows.nth(1)).toContainText('Buy')
  // Each figure is matched as its own cell, not as part of the row's text:
  // with some grants the balance after ends in another figure's digits.
  await expect(rows.nth(1).getByText('5.0000', { exact: true })).toBeVisible()
  // liquidity_b is 100 and nothing else traded against this market, so these
  // are exact, not just present — the one test that talks to the real ledger.
  await expect(rows.nth(1).getByText('0.5063', { exact: true })).toBeVisible()
  await expect(rows.nth(1).getByText('-2.5313', { exact: true })).toBeVisible()
  const balanceAfter = formatPreciseCredits(creditsLess(grant, '2.5313'))
  await expect(rows.nth(1).getByText(balanceAfter, { exact: true })).toBeVisible()
  await expect(rows.nth(2)).toContainText('Starting grant')
  await expect(rows.nth(2).getByText(formatSignedCredits(grant), { exact: true })).toBeVisible()
})

test('a trader who has never traded sees their starting grant, not the empty state', async ({ page }) => {
  // ADR 0009: the grant is minted lazily, on the first read of a balance or
  // a history — so even a trader who has never traded has exactly one row.
  const grant = await startingGrant()
  const trader = await registerTrader('th_empty')

  await logIn(page, trader)
  await page.getByRole('link', { name: 'History', exact: true }).click()

  const rows = page.getByRole('row')
  await expect(rows.nth(1)).toContainText('Starting grant')
  await expect(rows.nth(1).getByText(formatSignedCredits(grant), { exact: true })).toBeVisible()
})
