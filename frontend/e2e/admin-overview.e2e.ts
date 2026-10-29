import { expect, test } from './support/fixtures'
import { closeEarly, publishMarket, registerAdmin, saveDraft, uniqueName } from './support/backend'
import { logIn, marketCard, selectTab, setQueryParams } from './support/browser'

// [2.1] #5 through GET /markets/overview. Counts are not asserted here: other
// tests create markets at the same time, and both sides unit-test the counts.
// Every question carries this run's token and the list is searched for it
// (#493), so it holds this test's five markets whatever earlier runs left.
// The other admin's draft matches the search too, so its absence is the
// draft rule's doing and not the search's.
test('an admin sees every published market, only their own drafts, and can propose on a closed one', async ({ page }) => {
  const me = await registerAdmin('ov_me')
  const other = await registerAdmin('ov_other')
  const run = uniqueName('overview')
  const mine = await publishMarket(me, `Is ${run} mine open?`)
  const myDraft = await saveDraft(me, `Is ${run} my draft a draft?`)
  const theirs = await publishMarket(other, `Is ${run} theirs open?`)
  const theirDraft = await saveDraft(other, `Is ${run} their draft hidden?`)
  const closed = await publishMarket(me, `Has ${run} closed?`)
  await closeEarly(me, closed.id)

  await logIn(page, me)
  await setQueryParams(page, '/markets/overview', { q: run })
  await page.goto('/admin/markets')

  await expect(marketCard(page, mine.question)).toContainText('Created by you')
  const theirCard = marketCard(page, theirs.question)
  await expect(theirCard).toBeVisible()
  await expect(theirCard).not.toContainText('Created by you')

  await selectTab(page, /^draft/i)
  await expect(marketCard(page, myDraft.question)).toContainText('Created by you')
  await expect(page.getByText(theirDraft.question)).toHaveCount(0)

  await selectTab(page, /^closed/i)
  const closedCard = marketCard(page, closed.question)
  await expect(closedCard.getByRole('link', { name: 'Propose Outcome' })).toBeVisible()
  await expect(page.getByText(mine.question)).toHaveCount(0)
})
