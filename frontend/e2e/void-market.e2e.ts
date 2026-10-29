import { expect, test } from './support/fixtures'
import { buy, closeEarly, publishMarket, registerAdmin, registerTrader, uniqueName, voidMarket } from './support/backend'
import { loadUntilShown, logIn, selectTab } from './support/browser'

// [FE] #526, across market_service and the ledger: the control reads
// `voidable` from market_service's overview, the void itself is the ledger's
// own request (ADR 0025), and the refund comes back through the portfolio and
// the history. A unit test fakes each of those with MSW, so a field renamed
// on any side would still pass; this is the seam that can't see that.
// What the admin types, and what the market's page then shows a trader.
const REASON = 'Voided by the end-to-end test: the question had no answer.'

test('an admin voids a closed market, and a holder sees the refund in their portfolio and history', async ({ page, browser }) => {
  const admin = await registerAdmin('vm_admin')
  const trader = await registerTrader('vm_trader')
  const market = await publishMarket(admin, `Will ${uniqueName('void')} resolve yes?`)

  // liquidity_b is 100 and nothing else traded against this market, so this
  // buy costs exactly 2.5313. None of its shares are sold, so the void
  // refunds exactly what it was charged (ledger-service.md).
  await buy(trader, market, 'Yes', '5.0000')
  // An open market is closed early before it can be voided (#526).
  await closeEarly(admin, market.id)

  await logIn(page, admin)
  await page.goto('/admin/markets')
  await selectTab(page, /^closed/i)
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('button', { name: /^void market$/i }).click()

  const confirm = page.getByRole('button', { name: /confirm void/i })
  await expect(confirm).toBeDisabled()
  await page.getByRole('dialog', { name: 'Void market' }).getByLabel(/reason/i).fill(REASON)
  await confirm.click()

  // The status badge is the only "Voided" on the card.
  await expect(card.getByText('Voided', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: /^void market$/i })).toHaveCount(0)

  // The admin's own reads of the market say it was voided, and never that it
  // settled: both are the ledger's `result`, which a void sets to "voided".
  await page.goto(`/admin/markets/${market.id}/exposure`)
  await expect(page.getByText(
    'This market was voided, so it has no winner, and its pool has been emptied. What it refunded is in the audit entry for the void.',
    { exact: true },
  )).toBeVisible()
  await page.goto(`/admin/markets/${market.id}/price-history`)
  await expect(page.getByText(/this market was voided on .*, with no winner/i)).toBeVisible()

  const traderContext = await browser.newContext()
  const traderPage = await traderContext.newPage()
  await logIn(traderPage, trader)

  // The market's own page names the void and carries the admin's reason, and
  // offers no trade in place of the panel.
  await traderPage.goto(`/markets/${market.id}`)
  const voidedBanner = traderPage.getByRole('region', { name: 'Voided' })
  await expect(voidedBanner).toContainText('with no result')
  await expect(voidedBanner).toContainText(REASON)
  await expect(traderPage.getByText('This market has been voided. Trading is closed.', { exact: true })).toBeVisible()
  await expect(traderPage.getByRole('group', { name: 'Buy or sell' })).toHaveCount(0)

  await traderPage.goto('/portfolio')
  await expect(traderPage.getByText(market.question)).toBeVisible()
  await expect(traderPage.getByText('Refunded 2.5313', { exact: true })).toBeVisible()

  await traderPage.goto('/history')
  // Newest first: the refund is above the buy it pays back, and gives back
  // exactly what the buy took.
  const rows = traderPage.getByRole('row')
  const refundRow = rows.nth(1)
  await expect(refundRow).toContainText(market.question)
  await expect(refundRow).toContainText('Refund Yes')
  await expect(refundRow.getByText('5.0000', { exact: true })).toBeVisible()
  await expect(refundRow.getByText('+2.5313', { exact: true })).toBeVisible()
  await expect(rows.nth(2)).toContainText('Buy Yes')
  await expect(rows.nth(2).getByText('-2.5313', { exact: true })).toBeVisible()
  await traderContext.close()
})

// [FE] #526: a market voided while an admin has the propose form open. The
// void lands through the API after the page has drawn the form and before it
// is sent, so the proposal is refused as `market_already_voided` every run,
// rather than only when two admins happen to race.
test('a proposal sent after the market was voided says it was voided, not that it failed', async ({ page }) => {
  const admin = await registerAdmin('vm_propose')
  const market = await publishMarket(admin, `Will ${uniqueName('voidpropose')} resolve yes?`)
  await closeEarly(admin, market.id)

  await logIn(page, admin)
  await page.goto(`/admin/markets/${market.id}/propose-outcome`)
  await expect(page.getByRole('heading', { level: 1, name: 'Propose Outcome', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  await page.getByLabel('Written note').fill('Proposed by the end-to-end test after the market was voided.')

  await voidMarket(admin, market.id, 'Voided by the end-to-end test while the propose form was open.')
  await page.getByRole('button', { name: 'Propose Outcome', exact: true }).click()

  await expect(page.getByText('This market has been voided, so it takes no outcome.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Propose Outcome', exact: true })).toHaveCount(0)
})
