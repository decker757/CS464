import { expect, test } from '@playwright/test'
import { approveOutcome, backdateDisputeWindow, buy, closeEarly, proposeOutcome, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { loadUntilShown, logIn, selectTab } from './support/browser'

// [FE][3.4] #216, across market_service and the ledger: the settle control
// reads `settleable` from market_service, and settlement itself is the
// ledger's own request ([3.4] #12, ADR 0019). A unit test fakes both with
// MSW, so a field renamed on either side would still pass; this is the seam
// that can't see that.
test('an admin settles an approved market once its dispute window has passed, and a winning holder is paid', async ({ page, browser }) => {
  const admin = await registerAdmin('sm_admin')
  const approver = await registerAdmin('sm_approver')
  const trader = await registerTrader('sm_trader')
  const market = await publishMarket(admin, `Will ${uniqueName('settle')} resolve yes?`)

  await buy(trader, market, 'Yes', '10')
  await closeEarly(admin, market.id)
  const proposed = await proposeOutcome(admin, market, 'Yes')
  await approveOutcome(approver, market.id, proposed.proposal_id)

  // The window is a hard-coded five minutes past DISPUTE_WINDOW_SECONDS
  // (ADR 0019), too slow to wait out in CI — move the stored column into the
  // past instead of waiting for it.
  backdateDisputeWindow(market.id)

  await logIn(page, admin)
  await page.goto('/admin/markets')
  await selectTab(page, /^approved/i)
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('button', { name: /^settle$/i }).click()
  await page.getByRole('button', { name: /confirm settle/i }).click()

  await expect(card.getByText('Settled').first()).toBeVisible()
  await expect(card.getByRole('button', { name: /^settle$/i })).toHaveCount(0)

  const traderContext = await browser.newContext()
  const traderPage = await traderContext.newPage()
  await logIn(traderPage, trader)
  await traderPage.goto('/portfolio')

  await expect(traderPage.getByText(market.question)).toBeVisible()
  await expect(traderPage.getByText(/paid out/i)).toBeVisible()
  await traderContext.close()
})
