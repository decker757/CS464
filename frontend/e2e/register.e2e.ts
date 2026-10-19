import { expect, test } from './support/fixtures'
import { PASSWORD, registerTrader, uniqueName } from './support/backend'

// [A-1] #46 through the form rather than the API. Every other test registers
// its people through `support/backend.ts`, so until #341 nothing had ever
// filled this form against the real auth service — the one place the field
// names the browser sends and the ones auth reads have to agree.

test('a visitor fills in the form and arrives logged in', async ({ page }) => {
  const username = uniqueName('reg')

  await page.goto('/register')

  // The shell `index.html` serves. Nothing in Vitest renders it, so this is
  // the only test that would notice the tab going back to saying "frontend".
  await expect(page).toHaveTitle(/PredictSMU/)

  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Email').fill(`${username}@example.com`)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()

  // Registration logs the new trader straight in; GuestOnly owns the redirect.
  await expect(page).toHaveURL(/\/markets$/)
  await expect(page.getByText(username, { exact: true })).toBeVisible()
})

test('a username someone already has is refused, with the reason on the field', async ({ page }) => {
  const taken = await registerTrader('reg_taken')

  await page.goto('/register')
  await page.getByLabel('Username').fill(taken.username)
  await page.getByLabel('Email').fill(`${uniqueName('other')}@example.com`)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()

  // `duplicate_user` from the real auth service, shown against the field it is
  // about. A unit test asserts this against an MSW handler it writes itself;
  // this one asserts auth really sends those per-field `details`.
  //
  // `aria-invalid` is the part that pins it to the field: auth's one-field
  // message and its whole-form fallback are the same sentence, so asserting
  // the text alone would still pass if the `details` stopped arriving and the
  // message moved to the banner — the exact regression this is here for.
  await expect(page.getByLabel('Username')).toHaveAttribute('aria-invalid', 'true')
  // And the cursor is put on it: a refused submit moves focus to the first
  // field to fix, for a server's per-field error as much as for a local one.
  await expect(page.getByLabel('Username')).toBeFocused()
  await expect(page.getByText('That username is already registered.')).toBeVisible()
  await expect(page).toHaveURL(/\/register$/)
})
