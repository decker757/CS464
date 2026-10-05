import { expect, test } from '@playwright/test'
import { publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [X-1] #104: the browse page's Load more against the real keyset paging. Two
// things are added to each request on the way out, so the test does not depend
// on what else the database holds: `q`, this run's unique token, so only its
// own three markets are paged, and a page size of two. Everything else is real.
const PAGE_SIZE = 2

test('Load more adds the next page of markets and repeats none', async ({ page }) => {
  const admin = await registerAdmin('lm_admin')
  // Soonest-closing first: first, second, third. Hours apart, so none closes mid-test.
  const run = uniqueName('load_more')
  const firstQuestion = `Will ${run} come first?`
  const secondQuestion = `Will ${run} come second?`
  const thirdQuestion = `Will ${run} come third?`
  await publishMarket(admin, firstQuestion, undefined, { closesInHours: 24 })
  await publishMarket(admin, secondQuestion, undefined, { closesInHours: 25 })
  await publishMarket(admin, thirdQuestion, undefined, { closesInHours: 26 })
  const trader = await registerTrader('lm_trader')

  await page.route(
    url => url.pathname === '/public/markets',
    route => {
      const url = new URL(route.request().url())
      url.searchParams.set('limit', String(PAGE_SIZE))
      url.searchParams.set('q', run)
      return route.continue({ url: url.toString() })
    },
  )
  await logIn(page, trader)

  await expect(marketCard(page, firstQuestion)).toBeVisible()
  await expect(marketCard(page, secondQuestion)).toBeVisible()
  await expect(marketCard(page, thirdQuestion)).toHaveCount(0)

  await page.getByRole('button', { name: 'Load more' }).click()

  await expect(marketCard(page, thirdQuestion)).toBeVisible()
  // Appended, not replaced, and nothing shown twice.
  for (const question of [firstQuestion, secondQuestion, thirdQuestion]) {
    await expect(page.getByText(question, { exact: true })).toHaveCount(1)
  }
  // The real last page: nothing left to load.
  await expect(page.getByRole('button', { name: 'Load more' })).toHaveCount(0)
})
