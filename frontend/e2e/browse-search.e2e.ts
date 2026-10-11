import { expect, test } from '@playwright/test'
import { closeEarly, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard } from './support/browser'

// [FE][X-2] #35, through the real market service: GET /public/markets takes
// both q and status, so a unit test standing in for the server with MSW
// cannot catch a change to how either is read there. This does.
//
// Every step keeps this run's search applied, so the list only ever holds this
// run's two markets, however many other runs left in the database. That is why
// the status filter is cleared with its own chip and not with "Clear all": with
// no search the list is unscoped, and finding these markets again would mean
// paging through every other market that stopped more recently.
test('a trader searches the market list and narrows it by status', async ({ page }) => {
  const admin = await registerAdmin('bs_admin')
  const suffix = uniqueName('search')
  const first = await publishMarket(admin, `Will ${suffix} resolve yes?`)
  const second = await publishMarket(admin, `Is ${suffix} already decided?`)
  await closeEarly(admin, second.id)

  const trader = await registerTrader('bs_trader')
  await logIn(page, trader)
  await page.goto('/markets')

  // A search that matches nothing first. Both markets would be on the first
  // page of an unsearched list anyway, so only an empty answer proves the
  // search reached the server. A full page reload would also fail here, since
  // it wipes the search and shows the unsearched list.
  const search = page.getByRole('searchbox', { name: /search markets/i })
  await search.fill(`${suffix}-nomatch`)
  await page.keyboard.press('Enter')
  await expect(page.getByText('No markets match these filters.')).toBeVisible()

  await search.fill(suffix)
  await page.keyboard.press('Enter')
  await expect(marketCard(page, first.question)).toBeVisible()
  await expect(marketCard(page, second.question)).toBeVisible()

  await page.getByRole('button', { name: 'Open' }).click()
  await expect(marketCard(page, first.question)).toBeVisible()
  await expect(marketCard(page, second.question)).toHaveCount(0)

  await page.getByRole('button', { name: 'Clear status filter' }).click()
  await expect(marketCard(page, first.question)).toBeVisible()
  await expect(marketCard(page, second.question)).toBeVisible()
})
