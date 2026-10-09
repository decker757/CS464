import { expect, test } from '@playwright/test'
import { registerAdmin, saveDraft, submitMarket, uniqueName } from './support/backend'
import { logIn, marketCard, setQueryParams } from './support/browser'

// [2.1] #210, #235: Load more on the admin markets page against the real keyset
// paging. The overview has no search, so the test isolates itself another way:
// a fresh admin's Draft and Submitted tabs list only that admin's own markets,
// so they hold exactly what this test made, whatever else the database holds.
// One thing is added to each request on the way out, a page size of two.
// Everything else is real.
const PAGE_SIZE = 2

test('Load more adds the next page of an admin\'s markets, repeats none, and a new filter starts again', async ({ page }) => {
  const admin = await registerAdmin('aolm_admin')
  const run = uniqueName('admin_load_more')
  // Soonest close first: first, second, third. Hours apart, so the order is fixed.
  const firstDraft = `Will ${run} draft come first?`
  const secondDraft = `Will ${run} draft come second?`
  const thirdDraft = `Will ${run} draft come third?`
  const submitted = `Will ${run} be submitted?`
  await saveDraft(admin, firstDraft, { closesInHours: 24 })
  await saveDraft(admin, secondDraft, { closesInHours: 25 })
  await saveDraft(admin, thirdDraft, { closesInHours: 26 })
  await submitMarket(admin, submitted)

  await setQueryParams(page, '/markets/overview', { limit: String(PAGE_SIZE) })
  await logIn(page, admin)
  await page.goto('/admin/markets')
  const draftTab = page.getByRole('tab', { name: /^draft/i })
  const loadMore = page.getByRole('button', { name: 'Load more' })

  await draftTab.click()
  await expect(marketCard(page, firstDraft)).toBeVisible()
  await expect(marketCard(page, secondDraft)).toBeVisible()
  await expect(page.getByText(thirdDraft, { exact: true })).toHaveCount(0)

  await loadMore.click()

  await expect(marketCard(page, thirdDraft)).toBeVisible()
  // Appended, not replaced, and nothing shown twice.
  for (const question of [firstDraft, secondDraft, thirdDraft]) {
    await expect(page.getByText(question, { exact: true })).toHaveCount(1)
  }
  // The real last page: nothing left to load.
  await expect(loadMore).toHaveCount(0)

  // A new filter starts again from page one, carrying nothing over.
  await page.getByRole('tab', { name: /^submitted/i }).click()
  await expect(marketCard(page, submitted)).toBeVisible()
  await expect(page.getByText(firstDraft, { exact: true })).toHaveCount(0)

  await draftTab.click()
  await expect(marketCard(page, secondDraft)).toBeVisible()
  await expect(page.getByText(thirdDraft, { exact: true })).toHaveCount(0)
  await expect(loadMore).toBeVisible()
})
