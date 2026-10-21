import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { expect, test } from './support/fixtures'
import { logIn } from './support/browser'

// [FE][T-6] #61, through the real ledger and the real socket. The backend
// halves shipped without a browser test on purpose — there was no UI — so
// this is the first end-to-end cover of placing a limit order, seeing it
// rest, watching it fill from an `order` frame, and cancelling it
// (#347 and #378's reviews both asked for it here).
test('a trader rests a limit order, watches another trade fill it, and cancels the remainder', async ({ page }) => {
  const admin = await registerAdmin('lo_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('limit')} happen?`)
  const trader = await registerTrader('lo_trader')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  const availableBalance = page.getByLabel('available balance')
  await expect(availableBalance).toHaveText('1,000 credits')

  // A buy well under the current price (0.5000 on a fresh book) rests
  // instead of filling.
  await page.getByRole('button', { name: 'Limit' }).click()
  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  await page.getByLabel('Limit price').fill('0.30')
  await page.getByLabel('Quantity (shares)').fill('50')
  await page.getByRole('button', { name: /place buy order/i }).click()

  await expect(page.getByRole('status')).toContainText(/resting until the price reaches it/i)

  // The order's own budget — 0.30 × 50 — is reserved, so what is available
  // to spend drops by it while the balance itself does not (ADR 0024).
  await expect(availableBalance).toHaveText('985 credits')

  const orderRow = page.getByRole('row').filter({ hasText: '30.0%' })
  // Quantity, filled-so-far and what the order holds ([T-6] #26's fourth
  // criterion). Nothing has filled, so the whole budget is still reserved.
  await expect(orderRow).toContainText('50.0000')
  await expect(orderRow).toContainText('15.0000 credits')
  const filledCell = orderRow.getByRole('cell').nth(3)
  await expect(filledCell).toHaveText('0.0000')

  // Someone else sells the price down past 0.30, which fills part of the
  // resting order inside that trade's own transaction ([T-7] #27). The frame
  // that follows has to move the row with no reload.
  const other = await registerTrader('lo_other')
  await buy(other, market, 'No', '120.0000')

  // The frame moves filled-so-far off zero with no reload. The exact
  // quantity is the ledger's own closed-form partial fill, so this asserts
  // that it moved and that the order is still resting, not a figure that
  // would pin the LMSR's arithmetic.
  await expect(filledCell).not.toHaveText('0.0000', { timeout: 20_000 })
  // Still resting: a partial fill leaves the remainder on the book.
  await expect(page.getByText('Your open orders')).toBeVisible()

  // Cancelling releases what the remainder still held, so the order leaves
  // the live list for good.
  // Named after the order it cancels, so a page with several rows has one
  // unambiguous control per order rather than a row of identical "Cancel"s.
  await orderRow.getByRole('button', { name: /cancel buy yes at 30\.0%/i }).click()
  await expect(page.getByText('Your open orders')).toHaveCount(0)

  // The cancel released what the remainder was holding, so nothing of this
  // trader's is reserved any more and available is their whole balance
  // again — the shares the partial fill bought having been paid for.
  await expect(availableBalance).not.toHaveText('985 credits')
})

test('a limit order priced through the market fills on arrival', async ({ page }) => {
  const admin = await registerAdmin('lo_fill_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('fill')} happen?`)
  const trader = await registerTrader('lo_fill_trader')

  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  // Above the current price, so the ledger fills it at once rather than
  // resting it — the marketable-on-arrival path.
  await page.getByRole('button', { name: 'Limit' }).click()
  await page.getByRole('button', { name: 'Yes', exact: true }).click()
  await page.getByLabel('Limit price').fill('0.90')
  await page.getByLabel('Quantity (shares)').fill('10')
  await page.getByRole('button', { name: /place buy order/i }).click()

  await expect(page.getByRole('status')).toContainText(/filled in full/i)
  // Nothing rests, so there is no open-orders section at all.
  await expect(page.getByText('Your open orders')).toHaveCount(0)
})
