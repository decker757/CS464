import { expect, test } from '@playwright/test'
import { registerTrader } from './support/backend'
import { logIn } from './support/browser'

// [FE][L-1] #58, through the real ledger: GET /ledger/leaderboard is a unit
// test's own idea of what the ledger sends, fed through MSW, so a field
// renamed there would still pass every test in LeaderboardPage.test.tsx. The
// board only refreshes every LEADERBOARD_REFRESH_SECONDS (60 in
// .env.example), so this polls past one refresh rather than asserting on the
// first load.
test('the leaderboard renders a real ranking from the ledger', async ({ page }) => {
  test.setTimeout(120_000)
  const trader = await registerTrader('board')
  // Logging in reads the balance, which lazily mints the ledger account
  // (ADR 0009) — that is what makes this trader rankable, not a trade.
  await logIn(page, trader)

  await expect.poll(async () => {
    await page.goto('/leaderboard')
    return page.getByText(/Updated .* ranked/).isVisible()
  }, { timeout: 90_000, intervals: [5_000] }).toBe(true)

  await expect(page.getByRole('table')).toBeVisible()
  await expect(page.getByRole('row').first()).toBeVisible()
})
