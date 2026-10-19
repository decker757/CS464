import { expect, test } from './support/fixtures'
import { registerAdmin, uniqueName } from './support/backend'
import { logIn, marketCard, selectTab } from './support/browser'

// [1.1] #1 through the form. Every other test makes its markets through
// `support/backend.ts`, so until #341 no browser had ever opened the create
// page — and the autosave is the one piece of this app that writes to the
// backend with nobody pressing anything (ADR 0004, ADR 0008).

test('a question typed into the create form saves itself and appears under Drafts', async ({ page }) => {
  const admin = await registerAdmin('create')
  const question = `Is ${uniqueName('autosaved')} saved on its own?`

  await logIn(page, admin)
  await page.goto('/admin/markets/new')
  await page.getByLabel('Question *').fill(question)

  // The form posts every three seconds and says so. Nothing is clicked here:
  // that the draft reaches market_service untouched is the whole point.
  await expect(page.getByText('Saved')).toBeVisible({ timeout: 15_000 })

  await page.goto('/admin/markets')
  await selectTab(page, /^draft/i)
  await expect(marketCard(page, question)).toBeVisible()
})
