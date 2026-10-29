import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, startingGrant, uniqueName } from './support/backend'
import { logIn, outcomeChoice } from './support/browser'
import { creditsLess, creditsPlus, formatPreciseCredits, formatRoundedCredits } from './support/credits'

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

  await outcomeChoice(page, 'Yes').click()
  await page.getByLabel('Quantity (shares)').fill('10')
  const preview = page.getByLabel('trade preview')
  await expect(preview.getByText('8.8592 credits', { exact: true })).toBeVisible()
  // The unit is part of the row (#430), so match the whole string rather than
  // the figure alone: an exact matcher without it is what broke dev (#470).
  await expect(preview.getByText('0.8859 credits/share', { exact: true })).toBeVisible()
  await expect(preview).toContainText('88.1% → 89.1%')
  await page.getByRole('button', { name: /^buy yes$/i }).click()

  await expect(page.getByText('Bought 10 Yes for 8.8592 credits.', { exact: true })).toBeVisible()
  // The navbar shows the balance rounded to the cent, so this is the grant
  // less what the trade actually cost rather than the whole credits of it:
  // the fraction used to be dropped, which is #443.
  const afterTheBuy = creditsLess(grant, '8.8592')
  await expect(balance).toHaveText(`${formatRoundedCredits(afterTheBuy)} credits`)

  await page.getByRole('button', { name: /^sell$/i }).click()
  await page.getByLabel('Quantity (shares)').fill('4')
  await expect(page.getByLabel('trade preview')).toBeVisible()
  await page.getByRole('button', { name: /^sell yes$/i }).click()

  await expect(page.getByText('Sold 4 Yes for 3.5557 credits.', { exact: true })).toBeVisible()
  const afterTheSell = `${formatRoundedCredits(creditsPlus(afterTheBuy, '3.5557'))} credits`
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
  // is under 0.0000003, and the preview rounds a buy's cost up to four places
  // (ledger-service.md, GET /ledger/markets/{market_id}/preview, "The
  // rounding direction, and why it is not symmetric"), so it asks
  // q - 69.3147: more than the grant, whatever the grant is.
  const quantity = creditsPlus(grant, '2000')
  const cost = creditsLess(quantity, '69.3147')
  await page.getByLabel('Quantity (shares)').fill(quantity)
  await expect(page.getByLabel('trade preview')).toBeVisible()

  const refusal = `You have ${formatPreciseCredits(grant)} credits, but this trade needs ${formatPreciseCredits(cost)}.`
  await expect(page.getByText(refusal, { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /^buy yes$/i })).toBeDisabled()
  // Nothing was sent: the balance is still the untouched starting grant.
  await expect(page.getByLabel('available balance')).toHaveText(`${formatRoundedCredits(grant)} credits`)
})

// [FE][T-2] #451, through the real ledger: whether the trader holds any of an
// outcome is read from the portfolio the ledger returns, and an outcome never
// traded is simply absent from it. The unit tests fake that reply; this is
// the check that the real one leads to the sentence, and that the sentence
// gives way to the quantity field once the ledger reports a holding.
test('selling an outcome the trader holds none of offers to buy it instead', async ({ page }) => {
  const admin = await registerAdmin('tn_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('none')} happen?`)
  const trader = await registerTrader('tn_trader')
  // Some Yes and no No, so the empty holding sits beside one that is not.
  await buy(trader, market, 'Yes', '2')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  const buyToggle = page.getByRole('button', { name: 'Buy', exact: true })
  const noChoice = outcomeChoice(page, 'No')
  const quantity = page.getByLabel('Quantity (shares)')
  // The panel's own live region, so the sentence is both in the panel and
  // announced.
  const holdsNone = page.getByRole('status').filter({ hasText: "You don't hold any No shares." })

  await page.getByRole('button', { name: 'Sell', exact: true }).click()
  await noChoice.click()

  await expect(holdsNone).toBeVisible()
  await expect(quantity).toHaveCount(0)

  await page.getByRole('button', { name: 'Buy No instead' }).click()
  await expect(buyToggle).toHaveAttribute('aria-pressed', 'true')
  await expect(noChoice).toBeChecked()
  await expect(quantity).toBeFocused()

  await quantity.fill('1')
  await expect(page.getByLabel('trade preview')).toBeVisible()
  await page.getByRole('button', { name: /^buy no$/i }).click()
  await expect(page.getByText(/^Bought 1 No for /)).toBeVisible()

  await page.getByRole('button', { name: 'Sell', exact: true }).click()
  await expect(page.getByLabel('shares held')).toContainText('You hold 1.0000 shares')
  await expect(holdsNone).toHaveCount(0)
})

// #429 point 2, through the real ledger: the price a trader clicks is the
// outcome the panel trades. The unit tests fake the snapshot and the preview
// with MSW, so only this sees the ledger's own prices name the controls, and
// the outcome chosen on one reach the real preview and trade routes.
test("clicking an outcome's price makes it the outcome the panel trades and quotes", async ({ page }) => {
  const admin = await registerAdmin('tc_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('choose')} happen?`)
  const trader = await registerTrader('tc_trader')
  // 50 Yes shares at b = 100 (support/backend.ts) move the book to Yes 62.3%
  // and No 37.8%, so the two controls quote different prices.
  const other = await registerTrader('tc_other')
  await buy(other, market, 'Yes', '50.0000')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  const yes = outcomeChoice(page, 'Yes')
  const no = outcomeChoice(page, 'No')
  // The price is part of the control, not beside it.
  await expect(no).toHaveAccessibleName('No, 37.8%')
  await expect(yes).toBeChecked()

  await no.click()

  await expect(no).toBeChecked()
  await expect(yes).not.toBeChecked()
  await expect(page.getByText(/^Trading No\./)).toBeVisible()
  await page.getByLabel('Quantity (shares)').fill('10')
  await expect(page.getByLabel('trade preview')).toContainText('37.8% → ')
  await page.getByRole('button', { name: /^buy no$/i }).click()
  await expect(page.getByText(/^Bought 10 No for /)).toBeVisible()

  // A radio group, in the real browser under the styling that hides the
  // native circle: the arrow keys move the choice, and the panel with it.
  await no.focus()
  await page.keyboard.press('ArrowLeft')
  await expect(yes).toBeChecked()
  await expect(yes).toBeFocused()
  await expect(page.getByText(/^Trading Yes\./)).toBeVisible()
})
