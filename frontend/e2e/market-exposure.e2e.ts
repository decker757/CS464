import { expect, test } from './support/fixtures'
import { buy, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName } from './support/backend'
import { loadUntilShown, logIn } from './support/browser'

// [FE][2.2] #55, through the real ledger: a unit test fakes #6's response
// with MSW, so a field #6 renamed (as it did: exceeds_pool -> payout_exceeds_pool,
// max_payout -> payout_if_wins) would still pass there. Only the real
// endpoint catches that.
test('an admin sees shares outstanding for a traded outcome, with no flag', async ({ page }) => {
  const admin = await registerAdmin('exp_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('exposure')} resolve yes?`)
  const trader = await registerTrader('exp_trader')
  await buy(trader, market, 'Yes', '5')

  await logIn(page, admin)
  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('link', { name: 'Exposure' }).click()

  await expect(page.getByRole('heading', { name: market.question })).toBeVisible()
  // The only place state_version is rendered ([FE] #361). Every other test of
  // it fakes the response with MSW, which asserts this frontend's belief
  // about the shape rather than the ledger's, so a rename there would leave
  // "state version —" on the screen that gates a settlement and nothing else
  // would go red.
  await expect(page.getByText(/state version \d+/i)).toBeVisible()
  // Shares and payout are both 5 here, so check each column on its own: a
  // row-wide "contains 5.0000" passes with the payout column broken.
  await expect(page.getByRole('columnheader')).toHaveText(['Outcome', 'Shares outstanding', 'Payout if wins', 'Pool residue if wins'])
  const yesCells = page.getByRole('row', { name: /yes/i }).getByRole('cell')
  const sharesCell = yesCells.nth(1)
  const payoutCell = yesCells.nth(2)
  const residueCell = yesCells.nth(3)
  await expect(sharesCell).toHaveText('5.0000')
  await expect(payoutCell).toHaveText('5.0000')
  // The residue in the ledger's own words (#377). A unit test feeds the cell a
  // `residue_if_wins` this frontend wrote, so a field the ledger renamed would
  // leave a bare "—" here and nothing else would go red. "Pool keeps", because
  // the 250 seed subsidy covers a 5.0000 payout many times over.
  await expect(residueCell).toHaveText(/^Pool keeps \d+\.\d{4}$/)
  expect(await page.getByRole('alert').count()).toBe(0)
})

test('a market nobody has traded shows no trades yet, with its book open', async ({ page }) => {
  const admin = await registerAdmin('exp_empty_admin')
  const market = await publishMarket(admin, `Will ${uniqueName('untraded')} resolve yes?`)
  // #517. Every page that lists markets reads each one's snapshot, and the
  // first snapshot opens the market's book. Another worker's browse page may
  // do that before this test looks, or may not, so open it here: every run
  // then checks the same state. With no book the page reads `outcomes: []`,
  // which only a unit test can hold still.
  await snapshot(admin, market.id)

  await logIn(page, admin)
  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('link', { name: 'Exposure' }).click()

  // The book is open, and nothing has traded in it.
  await expect(page.getByText(/state version 0$/i)).toBeVisible()
  await expect(page.getByText(/no trades yet/i)).toBeVisible()
})
