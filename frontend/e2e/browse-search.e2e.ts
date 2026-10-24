import { expect, test } from './support/fixtures'
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

// #442, through the real market service: FastAPI ignores a query parameter it
// does not know, so if either side renamed `closes_from` or `closes_before`,
// the MSW tests on this side and the route tests on that side would both stay
// green while the preset quietly filtered nothing. Both markets are scheduled
// to close about sixty days out, which no "This month" window can reach from
// any day or timezone, so only the window reaching the server hides the
// first. The second is closed early, so trading stopped now, and the server
// finds it by that moment rather than by its schedule (market-service.md, "A
// market closed early is found by when it was closed"): it stays, which a
// window from the wrong month, or one the server dropped, would not leave.
// It would only miss within seconds of a month boundary. Nothing here asserts
// a market still trading that closes soon: other runs leave those behind.
test('a trader narrows the market list to what closes this month', async ({ page }) => {
  const admin = await registerAdmin('bc_admin')
  const suffix = uniqueName('closing')
  const later = await publishMarket(admin, `Will ${suffix} resolve in sixty days?`, undefined, { closesInHours: 60 * 24 })
  const stoppedNow = await publishMarket(admin, `Was ${suffix} stopped early?`, undefined, { closesInHours: 60 * 24 })
  await closeEarly(admin, stoppedNow.id)

  const trader = await registerTrader('bc_trader')
  await logIn(page, trader)
  await page.goto('/markets')

  const search = page.getByRole('searchbox', { name: /search markets/i })
  await search.fill(suffix)
  await page.keyboard.press('Enter')
  await expect(marketCard(page, later.question)).toBeVisible()
  await expect(marketCard(page, stoppedNow.question)).toBeVisible()

  const closing = page.getByRole('radiogroup', { name: 'Filter by closing time', exact: true })
  await closing.getByRole('radio', { name: 'This month', exact: true }).click()
  // The list remounts on a new preset and shows no cards while it loads, so
  // the kept market showing is what proves the filtered list has arrived,
  // and only then is the other one's absence worth reading.
  await expect(marketCard(page, stoppedNow.question)).toBeVisible()
  await expect(marketCard(page, later.question)).toHaveCount(0)
})
