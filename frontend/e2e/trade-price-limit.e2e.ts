import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, outcomeChoice } from './support/browser'

// [FE][T-2] #644, through the real ledger: a trade sends the most it will pay,
// and a price that moved by less than that between the quote and the click
// still executes. Without a limit the same click is refused as quote_stale.
//
// A fresh market at liquidity_b 100: 10 Yes costs about 5.12, and one share
// bought by somebody else first raises that by about 0.025, well inside the
// 1% the panel allows (about 0.05).
test('a trade still executes when another trade moved the price within its limit', async ({ page }) => {
  const admin = await registerAdmin('pl_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('limit')} happen?`)
  const trader = await registerTrader('pl_trader')
  const other = await registerTrader('pl_other')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)
  await outcomeChoice(page, 'Yes').click()
  await page.getByLabel('Quantity (shares)').fill('10')
  const preview = page.getByLabel('trade preview')
  await expect(preview.getByText('Pay at most')).toBeVisible()
  // The figure beside the preview's Cost label, wherever that row sits.
  const quotedCost = preview.getByText('Cost', { exact: true }).locator('xpath=following-sibling::*[1]')
  await expect(quotedCost).toHaveText(/^\d+\.\d{4} credits$/)
  const quoted = Number.parseFloat((await quotedCost.textContent()) ?? '')

  // Lands between this trader's quote and their click.
  await buy(other, market, 'Yes', '1')
  await page.getByRole('button', { name: /^buy yes$/i }).click()

  const bought = page.getByText(/^Bought 10 Yes for \d+\.\d{4} credits\.$/)
  await expect(bought).toBeVisible()
  await expect(page.getByText(/prices moved/i)).toHaveCount(0)
  // The trade went through at the price the other buy left, which is higher
  // than the quote, and not at the quote itself.
  const paid = Number.parseFloat(((await bought.textContent()) ?? '').replace('Bought 10 Yes for ', ''))
  expect(paid).toBeGreaterThan(quoted)
})
