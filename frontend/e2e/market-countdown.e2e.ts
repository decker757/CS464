import { expect, test } from './support/fixtures'
import { publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn } from './support/browser'

// #429, through market_service's own close_time: the market page's header
// counts down to the close on the browse card's clock, and keeps counting
// while the page is open. A countdown worked out once at render freezes, and
// a frozen one says a market is still trading after it has closed (ADR 0011).
test('the market page counts down to its close, and the countdown moves while the page stays open', async ({ page }) => {
  const admin = await registerAdmin('mc_admin')
  const trader = await registerTrader('mc_trader')
  const market = await publishMarket(admin, `Will ${uniqueName('countdown')} close within the hour?`, undefined, { closesInHours: 1 })

  // A fake clock that starts at the real time and runs at the real pace, so
  // ten minutes can pass at once below. Only the page's clock: the services
  // keep their own, and the session cookies expire on the browser's real one.
  await page.clock.install()
  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  const closeLabel = page.getByText(/^Closes in \d+m$/)
  await expect(closeLabel).toBeVisible()
  const minutesBefore = Number((await closeLabel.innerText()).replace(/\D/g, ''))

  // Ten minutes on, with no reload and nothing else for the page to re-render
  // for: only a timer on the page can move the label.
  await page.clock.fastForward('10:00')

  await expect(closeLabel).toHaveText(`Closes in ${minutesBefore - 10}m`)
})
