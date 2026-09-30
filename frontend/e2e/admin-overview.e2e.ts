import { expect, test } from '@playwright/test'
import { closeEarly, publishMarket, registerAdmin, saveDraft, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [2.1] #5 through GET /markets/overview. Counts are not asserted here: other
// tests create markets at the same time, and both sides unit-test the counts.
test('an admin sees every published market, only their own drafts, and can propose on a closed one', async ({ page }) => {
  const me = await registerAdmin('ov_me')
  const other = await registerAdmin('ov_other')
  const mine = await publishMarket(me, `Is ${uniqueName('mine')} open?`)
  const myDraft = await saveDraft(me, `Is ${uniqueName('mydraft')} a draft?`)
  const theirs = await publishMarket(other, `Is ${uniqueName('theirs')} open?`)
  const theirDraft = await saveDraft(other, `Is ${uniqueName('theirdraft')} hidden?`)
  const closed = await publishMarket(me, `Is ${uniqueName('closed')} closed?`)
  await closeEarly(me, closed.id)

  await logIn(page, me)
  await page.goto('/admin/markets')

  await expect(marketCard(page, mine.question)).toContainText('Created by you')
  await expect(marketCard(page, myDraft.question)).toContainText('Created by you')
  await expect(marketCard(page, theirs.question)).toBeVisible()
  await expect(marketCard(page, theirs.question)).not.toContainText('Created by you')
  await expect(page.getByText(theirDraft.question)).toHaveCount(0)

  await page.getByRole('tab', { name: /^closed/i }).click()
  await expect(marketCard(page, closed.question).getByRole('link', { name: 'Propose Outcome' })).toBeVisible()
  await expect(page.getByText(mine.question)).toHaveCount(0)
})
