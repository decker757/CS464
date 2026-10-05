import { expect, test } from '@playwright/test'
import { closeEarly, publishMarket, registerAdmin, uniqueName } from './support/backend'
import { loadUntilShown, logIn, marketCard } from './support/browser'

// [3.1] #9 and [3.2] #10, across market_service and the audit log the
// proposals page reads: one admin proposes, a second approves.
test('one admin proposes an outcome and a second admin approves it', async ({ browser }) => {
  const proposer = await registerAdmin('pa_prop')
  const approver = await registerAdmin('pa_appr')
  const market = await publishMarket(proposer, `Did ${uniqueName('resolve')} happen?`)
  await closeEarly(proposer, market.id)

  const proposerContext = await browser.newContext()
  const proposerPage = await proposerContext.newPage()
  await logIn(proposerPage, proposer)
  await proposerPage.goto('/admin/markets')
  // Paged (#210), and earlier runs fill the list: load until this market is shown.
  const card = await loadUntilShown(proposerPage, market.question)
  await card.getByRole('link', { name: 'Propose Outcome' }).click()
  await proposerPage.getByRole('button', { name: 'Yes', exact: true }).click()
  await proposerPage.getByLabel('Written note').fill('The end-to-end source says yes, so Yes wins.')
  await proposerPage.getByRole('button', { name: 'Propose Outcome' }).click()
  await expect(proposerPage).toHaveURL(/\/admin\/markets$/)

  await proposerContext.close()

  const approverContext = await browser.newContext()
  const approverPage = await approverContext.newPage()
  await logIn(approverPage, approver)
  await approverPage.goto('/admin/proposals')
  await expect(marketCard(approverPage, market.question)).toContainText(`Proposed by ${proposer.username}`)
  await marketCard(approverPage, market.question).getByRole('link', { name: 'Review' }).click()
  await approverPage.getByRole('button', { name: 'Approve' }).click()

  await expect(approverPage.getByRole('heading', { name: 'Proposal Approved' })).toBeVisible()
  // The "Approved by" line, not the navbar's username.
  await expect(approverPage.getByText(`${approver.username} on `)).toBeVisible()
  await approverContext.close()
})
