import { expect, test } from '@playwright/test'
import { registerTrader } from './support/backend'
import { logIn } from './support/browser'

// [A-2] #47 through the real auth service: the cookies it sets, and the
// session the frontend reads back from GET /auth/me.
test('a registered trader logs in and lands on the markets page as themselves', async ({ page }) => {
  const trader = await registerTrader('login')

  await logIn(page, trader)

  await expect(page.getByRole('heading', { name: 'Markets' })).toBeVisible()
  await expect(page.getByText(trader.username, { exact: true })).toBeVisible()
})
