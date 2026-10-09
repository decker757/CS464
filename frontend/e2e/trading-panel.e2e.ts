import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// [FE][T-2/T-3] #49, through the real ledger: the preview and trade routes,
// against a real book. Exactly what unit tests against MSW mocks can't
// catch — a request body or response field drifting from the backend would
// still pass those.
test('a trader buys, then sells part of it, through the real trading panel', async ({ page }) => {
  const admin = await registerAdmin('tp_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('trade')} happen?`)
  const trader = await registerTrader('tp_trader')
  // So the outcome's shares outstanding comfortably exceed what the main
  // trader oversells below — otherwise the preview refuses with the
  // market-wide insufficient_shares_outstanding before the trade route's
  // per-user holding check ever gets a chance to (ledger-service.md).
  const other = await registerTrader('tp_other')
  await buy(other, market, 'Yes', '200.0000')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  const balance = page.getByLabel('available balance')

  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  await page.getByLabel('Quantity (shares)').fill('10')
  const preview = page.getByLabel('trade preview')
  await expect(preview).toContainText('8.8592 credits')
  await expect(preview).toContainText('88.1% → 89.1%')
  await page.getByRole('button', { name: /^buy yes$/i }).click()

  await expect(page.getByText('Bought 10 Yes for 8.8592 credits.')).toBeVisible()
  await expect(balance).toHaveText('991 credits')

  await page.getByRole('button', { name: /^sell$/i }).click()
  await page.getByLabel('Quantity (shares)').fill('4')
  await expect(page.getByLabel('trade preview')).toBeVisible()
  await page.getByRole('button', { name: /^sell yes$/i }).click()

  await expect(page.getByText('Sold 4 Yes for 3.5557 credits.')).toBeVisible()
  await expect(balance).toHaveText('994 credits')

  await page.getByLabel('Quantity (shares)').fill('100')
  await expect(page.getByLabel('trade preview')).toBeVisible()
  await page.getByRole('button', { name: /^sell yes$/i }).click()

  await expect(page.getByText(/you hold 6\.0000 shares, but this sell asks for 100\.0000/i)).toBeVisible()
  await expect(page.getByText(/something went wrong/i)).not.toBeVisible()
})
