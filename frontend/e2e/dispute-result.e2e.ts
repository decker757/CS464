import { expect, test } from '@playwright/test'
import { approveOutcome, closeEarly, proposeOutcome, publishMarket, registerAdmin, uniqueName } from './support/backend'
import { loadUntilShown, logIn } from './support/browser'

// [FE][3.3] #59, through the real market service and the audit log: the
// dispute window and send-back-outcome are already on dev, and the modal's
// content comes from an audit entry MSW only ever fakes. Any administrator
// may dispute, the proposer included (market-service.md's dispute-screen
// notes, point 2) — this test uses the proposer themselves, the case the
// unit tests cannot cover since the row has no way to know who approved.
test('the proposer disputes an approved result, and the market goes back to closed with Propose Outcome available again', async ({ browser }) => {
  const proposer = await registerAdmin('dr_prop')
  const approver = await registerAdmin('dr_appr')
  const market = await publishMarket(proposer, `Did ${uniqueName('dispute')} happen?`)
  await closeEarly(proposer, market.id)
  const proposed = await proposeOutcome(proposer, market, 'Yes')
  await approveOutcome(approver, market.id, proposed.proposal_id)

  const page = await (await browser.newContext()).newPage()
  await logIn(page, proposer)
  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)

  await expect(card.getByRole('button', { name: /dispute result/i })).toBeVisible()
  await card.getByRole('button', { name: /dispute result/i }).click()

  const dialog = page.getByRole('dialog', { name: /dispute result/i })
  await expect(dialog).toContainText('Yes')
  await expect(dialog).toContainText(proposer.username)
  await expect(dialog).toContainText(approver.username)

  await page.getByLabel(/reason/i).fill('Disputed by the end-to-end test.')
  await page.getByRole('button', { name: /confirm dispute/i }).click()

  await expect(card.getByText('Closed').first()).toBeVisible()
  await expect(card.getByRole('link', { name: 'Propose Outcome' })).toBeVisible()
  await expect(card.getByRole('button', { name: /dispute result/i })).toHaveCount(0)
})
