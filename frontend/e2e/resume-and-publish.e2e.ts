import { expect, test } from './support/fixtures'
import { registerAdmin, saveDraft, submitMarket, uniqueName } from './support/backend'
import { logIn, marketCard, selectTab } from './support/browser'

// #348. Every other test publishes over the API (`support/backend.ts`'s
// `publishMarket`), so no browser had ever carried a market from the create
// form to a trader — and both halves of this bug survived a unit suite and a
// backend suite for that reason: a unit test fakes market_service with MSW,
// and a backend test has no reload to lose the Publish button to.
//
// Every market here is one administrator's own, freshly registered for the
// test, so the Draft and Submitted tabs hold nothing but this test's rows and
// need no paging ([2.1] #5 scopes both to the caller).

test('a draft typed on the create page gets its own address, and both ways back reach the same market', async ({ page }) => {
  const admin = await registerAdmin('rp_resume')
  const question = `Is ${uniqueName('abandoned')} resumed rather than duplicated?`
  const renamed = `Is ${uniqueName('resumed')} the very same market?`

  await logIn(page, admin)
  await page.goto('/admin/markets/new')
  await page.getByLabel('Question *').fill(question)
  // Nothing is clicked: the three-second autosave is what creates the market
  // (ADR 0004).
  await expect(page.getByText('Saved')).toBeVisible({ timeout: 15_000 })

  // #348's second repro started here, with the address bar still reading
  // /admin/markets/new: coming back to that page minted a fresh draft_key, so
  // the next keystroke created a second market. The first save moves the page
  // to the market's own address, which makes the browser itself the resume
  // point — a reload of this URL continues the market rather than starting
  // one. (/admin/markets/new still means a new market, deliberately.)
  await expect(page).toHaveURL(/\/admin\/markets\/[\w-]+\/edit$/)
  const marketUrl = page.url()

  // Leave the way an admin who is called away leaves, and come back the two
  // ways there are. The address itself first.
  await page.goto('/admin/markets')
  await page.goto(marketUrl)
  await expect(page.getByLabel('Question *')).toHaveValue(question)

  // Then the list's own control, which must land on that same market.
  await page.goto('/admin/markets')
  await selectTab(page, /^draft/i)
  await marketCard(page, question).getByRole('link', { name: 'Resume draft' }).click()
  await expect(page).toHaveURL(marketUrl)
  // Filled from the server, not from anything this browser was still holding.
  await expect(page.getByLabel('Question *')).toHaveValue(question)

  await page.getByLabel('Question *').fill(renamed)
  await expect(page.getByText('Saved')).toBeVisible({ timeout: 15_000 })

  // One market, renamed. Renaming cannot hide a duplicate: a second market
  // would still be sitting on this tab under the first question, which is
  // exactly what the per-mount draft_key used to leave behind.
  await page.goto('/admin/markets')
  await selectTab(page, /^draft/i)
  await expect(marketCard(page, renamed)).toBeVisible()
  await expect(page.getByText(question)).toHaveCount(0)
})

test('an admin publishes a market submitted in the form, after reloading the page', async ({ page }) => {
  const admin = await registerAdmin('rp_publish')
  const question = `Will ${uniqueName('reloaded')} reach a trader?`
  // Submission needs every term, which the API already fills; what the browser
  // is here for is the submit, the reload and the publish.
  const draft = await saveDraft(admin, question)

  await logIn(page, admin)
  await page.goto(`/admin/markets/${draft.id}/edit`)
  await expect(page.getByLabel('Question *')).toHaveValue(question)

  await page.getByRole('button', { name: /submit for review/i }).click()
  await expect(page.getByRole('button', { name: /publish market/i })).toBeVisible()

  // The whole ticket. The Publish button used to come from the save response
  // this session happened to be holding, so a reload lost it and nothing
  // anywhere else in the app could ever publish this market.
  await page.reload()

  await expect(page.getByLabel('Question *')).toHaveValue(question)
  // Published or submitted, this form is read-only (ADR 0008).
  await expect(page.getByLabel('Question *')).toBeDisabled()
  await page.getByRole('button', { name: /publish market/i }).click()

  // Publishing sends the admin to the trader list, and the market is on it:
  // the only proof that market_service wrote OPEN, rather than a button
  // merely stopping complaining.
  await expect(page).toHaveURL(/\/markets$/)
  await page.getByRole('searchbox', { name: /search markets/i }).fill(question)
  await page.keyboard.press('Enter')
  await expect(marketCard(page, question)).toBeVisible()
})

test('an admin publishes a submitted market from the markets list, which offers no draft control', async ({ page }) => {
  const admin = await registerAdmin('rp_list')
  const question = `Will ${uniqueName('listpublish')} go live from the list?`
  await submitMarket(admin, question)

  await logIn(page, admin)
  await page.goto('/admin/markets')
  await selectTab(page, /^submitted/i)
  const card = marketCard(page, question)
  // Past draft, so the form may no longer write to it and the list must not
  // offer to reopen it (ADR 0008, the ticket's third criterion).
  await expect(card.getByRole('link', { name: 'Resume draft' })).toHaveCount(0)

  await card.getByRole('button', { name: /^publish$/i }).click()
  await page.getByRole('button', { name: /confirm publish/i }).click()

  await expect(card.getByText('Open', { exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: /^publish$/i })).toHaveCount(0)
})
