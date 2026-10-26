import { expect, test } from './support/fixtures'
import { buy, closeEarly, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { loadUntilShown, logIn, selectTab } from './support/browser'

// [FE] #526, across market_service and the ledger: the control reads
// `voidable` from market_service's overview, the void itself is the ledger's
// own request (ADR 0025), and the refund comes back through the portfolio and
// the history. A unit test fakes each of those with MSW, so a field renamed
// on any side would still pass; this is the seam that can't see that.
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
  await page.getByRole('dialog', { name: 'Void market' }).getByLabel(/reason/i).fill('Voided by the end-to-end test: the question had no answer.')
  await confirm.click()

  // The status badge is the only "Voided" on the card.
  await expect(card.getByText('Voided', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: /^void market$/i })).toHaveCount(0)

  const traderContext = await browser.newContext()
  const traderPage = await traderContext.newPage()
  await logIn(traderPage, trader)

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
