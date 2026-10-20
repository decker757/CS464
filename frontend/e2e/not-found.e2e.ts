import { expect, test } from './support/fixtures'
import { registerTrader } from './support/backend'
import { logIn } from './support/browser'
import { NOT_FOUND_PROBE } from './support/routeCoverage'

// [FE] #355, in a real browser against the served build. A unit test opens
// an unknown address on a router inside jsdom; only a browser asks the
// server for it first, so only this proves the build answers a deep link it
// has never heard of with the app, and the app with its 404 page, rather
// than with a blank page.
test('a signed-in trader who opens a bad address gets the 404 page and a way back', async ({ page }) => {
  const trader = await registerTrader('notfound')
  await logIn(page, trader)

  // The probe address, not any typo: the route guard reports every other
  // address only the catch-all matches, as a route it can no longer read.
  await page.goto(NOT_FOUND_PROBE)

  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
  // Still signed in, with the navigation, after a full page load. Waiting
  // for it is also waiting for the session lookup: until it answers, the link
  // below points at the landing page, as it does for a guest.
  await expect(page.getByRole('link', { name: 'Portfolio', exact: true })).toBeVisible()

  await page.getByRole('link', { name: 'Go to the home page' }).click()

  await expect(page).toHaveURL(/\/markets$/)
  await expect(page.getByRole('heading', { name: 'Markets' })).toBeVisible()
})
