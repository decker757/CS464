import { expect, test } from './support/fixtures'
import { PASSWORD, registerTrader } from './support/backend'

// [A-2] #601 against the real limiter: five failed logins on one username
// use up its allowance, and the sixth is refused. The form has to say how
// long to wait rather than call the password wrong. A fresh trader per run,
// so nothing else's attempts count against this one.
const FAILED_LOGINS_ALLOWED = 5

test('a trader locked out by failed logins is told how long to wait', async ({ page }) => {
  const trader = await registerTrader('rate_limit')

  await page.goto('/login')
  await page.getByLabel('Username or Email').fill(trader.username)
  await page.getByLabel('Password', { exact: true }).fill(`not-${PASSWORD}`)
  for (let attempt = 1; attempt <= FAILED_LOGINS_ALLOWED; attempt++) {
    await page.getByRole('button', { name: /log in/i }).click()
    await expect(page.getByRole('alert')).toHaveText('Incorrect username or password.')
  }

  await page.getByRole('button', { name: /log in/i }).click()

  await expect(page.getByRole('alert')).toContainText('Too many attempts. Try again in')
  await expect(page.getByRole('alert')).toContainText('minutes')
  await expect(page.getByRole('button', { name: /log in/i })).toBeEnabled()
})
