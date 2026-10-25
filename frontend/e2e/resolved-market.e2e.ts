import { expect, test } from './support/fixtures'
import { approveOutcome, buy, closeEarly, proposeOutcome, publishMarket, registerAdmin, registerTrader, snapshot, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// #429 point 3, across market_service and the ledger: the result comes from
// the public market read and the last trade's price from the ledger's
// snapshot, and an approved market used to show the second under the first —
// "Approved outcome: Yes" above Yes at well under half. A unit test fakes both
// reads with MSW; this one has the real ones disagree.
test('an approved market shows its result where the price was, not the last trade', async ({ page }) => {
  const proposer = await registerAdmin('rm_prop')
  const approver = await registerAdmin('rm_appr')
  const trader = await registerTrader('rm_trader')
  const market = await publishMarket(proposer, `Will ${uniqueName('resolved')} say yes?`)

  // The crowd backs No, and then Yes wins: the case that read backwards.
  await buy(trader, market, 'No', '30')
  const no = market.outcomes.find(outcome => outcome.label === 'No')
  const lastTrade = await snapshot(trader, market.id)
  // 30 No shares at b = 100 (support/backend.ts) move No to 0.5744.
  expect(lastTrade.prices.find(price => price.outcome_id === no?.id)?.price).toBe('0.5744')

  await closeEarly(proposer, market.id)
  const proposed = await proposeOutcome(proposer, market, 'Yes')
  await approveOutcome(approver, market.id, proposed.proposal_id)

  await logIn(page, trader)
  // The detail page's own read: logIn lands on /markets, where this market's
  // card fetches the same snapshot URL.
  const snapshotRead = page.waitForResponse(
    response => response.url().endsWith(`/ledger/markets/${market.id}/snapshot`) && new URL(page.url()).pathname === `/markets/${market.id}`,
  )
  await page.goto(`/markets/${market.id}`)

  await expect(page.getByLabel('Yes result', { exact: true })).toHaveText('Won')
  await expect(page.getByLabel('No result', { exact: true })).toHaveText('Lost')
  // The page has the last trade's price once this lands, so an absence
  // checked after it is one the page chose rather than one it had not reached.
  await snapshotRead
  await expect(page.getByText('implied probability', { exact: true })).toHaveCount(0)
  await expect(page.getByText('57.4%', { exact: true })).toHaveCount(0)
})
