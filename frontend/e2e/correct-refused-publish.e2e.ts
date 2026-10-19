import { expect, test } from './support/fixtures'
import { registerAdmin, submitMarket, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// #381. Publishing re-runs every submission rule against the clock (ADR 0008),
// so a market that sat submitted past its own close time is refused with
// `draft_incomplete`. market_service accepts a corrected resubmission of a
// submitted market, and the form used to stay read-only with the reason
// painted under a field nobody could edit. No other end-to-end test reaches
// `draft_incomplete` at all, so this is also the only browser that has seen
// that code come back from the real service.

const CLOSES_IN_SECONDS = 5

// A datetime-local input takes local wall-clock time to the minute. The
// browser and this process share the machine's timezone.
function toDateTimeInput(when: Date): string {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
    + `T${pad(when.getHours())}:${pad(when.getMinutes())}`
}

test('an admin corrects a close time that passed while the market sat submitted, and publishes it', async ({ page }) => {
  const admin = await registerAdmin('fix_publish')
  const question = `Will ${uniqueName('stale')} be published after its correction?`
  // Submission checks the close time against the clock too, so it has to be
  // in the future now and in the past by the time Publish is pressed.
  const market = await submitMarket(admin, question, { closesInHours: CLOSES_IN_SECONDS / 3600 })
  // Read after the save, so it is no earlier than the close time it stored.
  const closedBy = Date.now() + CLOSES_IN_SECONDS * 1000

  await logIn(page, admin)
  await page.goto(`/admin/markets/${market.id}/edit`)
  await expect(page.getByLabel('Question *')).toHaveValue(question)
  // Submitted, so read-only until a publish is refused (ADR 0008).
  await expect(page.getByLabel('Closes at *')).toBeDisabled()

  // The service reads its own clock, so wait on the time itself, with a
  // second's margin, rather than on a fixed pause.
  await expect.poll(() => Date.now(), { timeout: 15_000 }).toBeGreaterThan(closedBy + 1000)

  await page.getByRole('button', { name: /publish market/i }).click()

  // The real service's reason, under the field it names, which is now editable.
  await expect(page.getByText('Close time must be in the future.')).toBeVisible()
  await expect(page.getByLabel('Closes at *')).toBeEnabled()

  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000)
  await page.getByLabel('Closes at *').fill(toDateTimeInput(tomorrow))
  await page.getByRole('button', { name: /resubmit for review/i }).click()

  await expect(page.getByLabel('Closes at *')).toBeDisabled()
  await page.getByRole('button', { name: /publish market/i }).click()

  // Publishing sends the admin to the trader list, and the market is on it:
  // the only proof that market_service took the correction and wrote OPEN.
  await expect(page).toHaveURL(/\/markets$/)
  await page.getByRole('searchbox', { name: /search markets/i }).fill(question)
  await page.keyboard.press('Enter')
  await expect(marketCard(page, question)).toBeVisible()
})
