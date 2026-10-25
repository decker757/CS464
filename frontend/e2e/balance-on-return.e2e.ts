import { expect, test } from './support/fixtures'
import { approveOutcome, backdateDisputeWindow, buy, closeEarly, proposeOutcome, publishMarket, registerAdmin, registerTrader, settle, uniqueName } from './support/backend'
import { logIn } from './support/browser'
import { creditsPlus, formatRoundedCredits } from './support/credits'

// #511, across the ledger and the header: a payout is written by an
// administrator's request, and nothing tells the trader's tab about it — the
// socket has no payout frame. So the header re-reads the balance when the
// trader comes back to the tab. A unit test fires the event at an MSW fake;
// this is the real payout reaching the real header without a reload.
test("a payout reaches the trader's header when they come back to the tab, with no reload", async ({ page }) => {
  const admin = await registerAdmin('br_admin')
  const approver = await registerAdmin('br_approver')
  const trader = await registerTrader('br_trader')
  const market = await publishMarket(admin, `Will ${uniqueName('return')} resolve yes?`)

  await buy(trader, market, 'Yes', '10')
  await closeEarly(admin, market.id)
  const proposed = await proposeOutcome(admin, market, 'Yes')
  await approveOutcome(approver, market.id, proposed.proposal_id)
  // Settling waits out the dispute window and five minutes more (ADR 0019).
  backdateDisputeWindow(market.id)

  // The browse page, not the market's own: the market page's order frame
  // refetches the balance by itself, and would pass this without the fix.
  await logIn(page, trader)
  const header = page.getByLabel('available balance')
  await expect(header).toHaveText(/^[\d,.]+ credits$/)
  const before = (await header.innerText()).replace(' credits', '').replaceAll(',', '')

  await settle(admin, market.id)

  // Ten winning shares pay ten credits. Coming back to the tab, as clicking
  // into this window from another one does.
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect(header).toHaveText(`${formatRoundedCredits(creditsPlus(before, '10'))} credits`)
})
