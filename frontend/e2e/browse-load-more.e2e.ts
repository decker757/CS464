import { expect, test } from '@playwright/test'
import { publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [X-1] #104: the browse page's Load more against the real keyset paging. The
// page size is cut to two on the way out, so the test does not depend on how
// many markets the database holds; every other part of each request is real.
const PAGE_SIZE = 2

test('Load more adds the next page of markets and repeats none', async ({ page }) => {
  const admin = await registerAdmin('lm_admin')
  // The list is soonest-closing first, and these close within minutes, ahead
  // of any other market a local database is likely to hold: first, second, third.
  const run = uniqueName('load_more')
  const first = await publishMarket(admin, `Will ${run} come first?`, undefined, { closesInHours: 0.05 })
  const second = await publishMarket(admin, `Will ${run} come second?`, undefined, { closesInHours: 0.06 })
  const third = await publishMarket(admin, `Will ${run} come third?`, undefined, { closesInHours: 0.07 })
  const trader = await registerTrader('lm_trader')

  await page.route(
    url => url.pathname === '/public/markets',
    route => {
      const url = new URL(route.request().url())
      url.searchParams.set('limit', String(PAGE_SIZE))
      return route.continue({ url: url.toString() })
    },
  )
  await logIn(page, trader)

  await expect(marketCard(page, first.question)).toBeVisible()
  await expect(marketCard(page, second.question)).toBeVisible()
  await expect(marketCard(page, third.question)).toHaveCount(0)

  await page.getByRole('button', { name: 'Load more' }).click()

  await expect(marketCard(page, third.question)).toBeVisible()
  // Appended, not replaced, and nothing shown twice.
  await expect(page.getByText(first.question, { exact: true })).toHaveCount(1)
  await expect(page.getByText(second.question, { exact: true })).toHaveCount(1)
  await expect(page.getByText(third.question, { exact: true })).toHaveCount(1)
})
