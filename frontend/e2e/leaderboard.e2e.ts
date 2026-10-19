import { expect, test } from './support/fixtures'
import { registerTrader } from './support/backend'
import { logIn } from './support/browser'

// [FE][L-1] #58 and [FE][L-2] #39, through the real ledger: GET
// /ledger/leaderboard is a unit test's own idea of what the ledger sends, fed
// through MSW, so a field renamed there would still pass every test in
// LeaderboardPage.test.tsx. The board only refreshes every
// LEADERBOARD_REFRESH_SECONDS (60 in .env.example), so this polls past one
// refresh rather than asserting on the first load.
test('the leaderboard renders a real ranking from the ledger', async ({ page }) => {
  test.setTimeout(120_000)
  const trader = await registerTrader('board')
  // Logging in reads the balance, which lazily mints the ledger account
  // (ADR 0009) — that is what makes this trader rankable, not a trade.
  await logIn(page, trader)

  // A fresh trader only joins the board on its next refresh. The card shows
  // once `me` arrives with a rank in it, not merely once the page has loaded.
  await expect.poll(async () => {
    await page.goto('/leaderboard')
    // isVisible() never throws, so a board that is not computed yet is just
    // another poll; an assertion in here would fail the whole test instead.
    return page.getByText(/Your rank: #\d+/).isVisible()
  }, { timeout: 90_000, intervals: [5_000] }).toBe(true)

  // A trader with no positions is ranked at their balance, STARTING_CREDITS
  // (1000 in .env.example), so these come from the ledger's own figures: a
  // renamed `net_worth` would render "—" and fail here.
  await expect(page.getByText(/Net worth: [\d,]+\.\d{4} credits/)).toBeVisible()
  const myRow = page.locator('tr', { hasText: trader.username })
  await expect(myRow).toContainText('1,000.0000')
  await expect(myRow).toContainText('(you)')
})
