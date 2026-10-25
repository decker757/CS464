import { expect, test } from './support/fixtures'
import { publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn } from './support/browser'

const MINUTE_MS = 60_000

// #429, through market_service's own close_time: the market page's header
// counts down to the close on the browse card's clock, and keeps counting
// while the page is open. A countdown worked out once at render freezes, and
// a frozen one says a market is still trading after it has closed (ADR 0011).
test('the market page counts down to its close, and the countdown moves while the page stays open', async ({ page }) => {
  const admin = await registerAdmin('mc_admin')
  const trader = await registerTrader('mc_trader')
  const market = await publishMarket(admin, `Will ${uniqueName('countdown')} close within the hour?`, undefined, { closesInHours: 1 })

  // A fake clock set thirty and a half minutes before the market's own
  // close_time, then running at the real pace. Both figures below are worked
  // out from that, not read off the page: the half minute keeps the real
  // seconds this test takes from ticking the countdown over a minute boundary.
  // Only the page's clock: the services keep their own, and the session
  // cookies expire on the browser's real one.
  const closeMs = new Date(market.close_time).getTime()
  await page.clock.install({ time: closeMs - 30.5 * MINUTE_MS })
  await logIn(page, trader)
  await page.goto(`/markets/${market.id}`)

  const closeLabel = page.getByText(/^Closes in \d+m$/)
  await expect(closeLabel).toHaveText('Closes in 30m')

  // Ten minutes on, with no reload and nothing else for the page to re-render
  // for: only a timer on the page can move the label.
  await page.clock.fastForward('10:00')

  await expect(closeLabel).toHaveText('Closes in 20m')
})
