import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, startingGrant, uniqueName } from './support/backend'
import { logIn } from './support/browser'
import { addCredits, formatPreciseCredits, formatWholeCredits, subtractCredits } from './support/credits'

// [FE][T-2/T-3] #49, through the real ledger: the preview and trade routes,
// against a real book. Exactly what unit tests against MSW mocks can't
// catch — a request body or response field drifting from the backend would
// still pass those.
test('a trader buys, then sells part of it, through the real trading panel', async ({ page }) => {
  const grant = await startingGrant()
  const admin = await registerAdmin('tp_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('trade')} happen?`)
  const trader = await registerTrader('tp_trader')
  // So the outcome's shares outstanding comfortably exceed what the main
  // trader oversells below. A sell is priced against those, not against the
  // seller's own position (ledger-service.md), which is the whole reason the
  // panel clamps: without this buy the preview would refuse the oversell as
  // insufficient_shares_outstanding, and the quote asserted down there —
  // a confident price for shares the trader does not hold — would never
  // appear.
  const other = await registerTrader('tp_other')
  await buy(other, market, 'Yes', '200.0000')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  const balance = page.getByLabel('available balance')

  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  await page.getByLabel('Quantity (shares)').fill('10')
  const preview = page.getByLabel('trade preview')
  await expect(preview).toContainText('8.8592 credits')
  await expect(preview).toContainText('0.8859')
  await expect(preview).toContainText('88.1% → 89.1%')
  await page.getByRole('button', { name: /^buy yes$/i }).click()

  await expect(page.getByText('Bought 10 Yes for 8.8592 credits.')).toBeVisible()
  // The navbar shows whole credits, the fraction dropped.
  const afterTheBuy = subtractCredits(grant, '8.8592')
  await expect(balance).toHaveText(`${formatWholeCredits(afterTheBuy)} credits`)

  await page.getByRole('button', { name: /^sell$/i }).click()
  await page.getByLabel('Quantity (shares)').fill('4')
  await expect(page.getByLabel('trade preview')).toBeVisible()
  await page.getByRole('button', { name: /^sell yes$/i }).click()

  await expect(page.getByText('Sold 4 Yes for 3.5557 credits.')).toBeVisible()
  const afterTheSell = `${formatWholeCredits(addCredits(afterTheBuy, '3.5557'))} credits`
  await expect(balance).toHaveText(afterTheSell)

  // The clamp reads the position the ledger reports after that sell, so this
  // is also the one check that the holding is re-read once a trade commits
  // (#357): six shares left of the ten bought, and the sell cannot be sent.
  await expect(page.getByText(/you hold 6\.0000 shares/i)).toBeVisible()
  await page.getByLabel('Quantity (shares)').fill('100')

  // The ledger still quotes this sell: it prices against the shares
  // outstanding and has no holdings check at preview (ledger-service.md), so
  // a confident price for 100 shares the trader does not own is exactly what
  // the clamp is here to stop. The panel is what refuses it now.
  await expect(page.getByLabel('trade preview')).toBeVisible()
  await expect(page.getByText(/you hold 6\.0000 shares, but this sell asks for 100\.0000/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /^sell yes$/i })).toBeDisabled()
  await expect(balance).toHaveText(afterTheSell)
})

// [FE][T-2] #357's second finding, end to end: the balance comes from the
// ledger and the cost from its preview route, so only a real browser against
// a real stack proves the panel compares the two the ledger's own way and
// stops the trade before it is ever sent.
test('a buy the trader cannot afford is refused before the request', async ({ page }) => {
  const grant = await startingGrant()
  const admin = await registerAdmin('tb_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('budget')} happen?`)
  const trader = await registerTrader('tb_trader')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  // Two thousand shares more than the grant has credits. On an untouched
  // two-outcome market at b = 100, buying q shares of one outcome costs
  // q - 100 ln 2 + 100 ln(1 + e^(-q/100)). Past 2,000 shares that last term
  // is under 0.0000003, and the ledger rounds a buy's cost up to four places
  // (ledger-service.md, POST .../trades), so it asks q - 69.3147: more than
  // the grant, whatever the grant is.
  const quantity = addCredits(grant, '2000')
  const cost = subtractCredits(quantity, '69.3147')
  await page.getByLabel('Quantity (shares)').fill(quantity)
  await expect(page.getByLabel('trade preview')).toBeVisible()

  const refusal = `You have ${formatPreciseCredits(grant)} credits, but this trade needs ${formatPreciseCredits(cost)}.`
  await expect(page.getByText(refusal, { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /^buy yes$/i })).toBeDisabled()
  // Nothing was sent: the balance is still the untouched starting grant.
  await expect(page.getByLabel('available balance')).toHaveText(`${formatWholeCredits(grant)} credits`)
})
