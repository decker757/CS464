import { expect, test } from '@playwright/test'
import { buy, publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { loadUntilShown, logIn } from './support/browser'

// [FE][2.3] #56, through the real market service: POST /markets/{id}/close
// is already on dev, so this does not wait on a backend ticket. Any admin can
// close any market's trading, not only its creator (market-service.md's
// notes for #56, point 7), so this uses two separate admins.
test('any admin can close another admin\'s market early, and new trades are then refused', async ({ page }) => {
  const creator = await registerAdmin('cm_creator')
  const closer = await registerAdmin('cm_closer')
  const market = await publishMarket(creator, `Will ${uniqueName('closeearly')} resolve yes?`)

  await logIn(page, closer)
  await page.goto('/admin/markets')
  const card = await loadUntilShown(page, market.question)
  await card.getByRole('button', { name: /^close$/i }).click()

  const confirm = page.getByRole('button', { name: /confirm close/i })
  await expect(confirm).toBeDisabled()
  await page.getByLabel(/reason/i).fill('short')
  await expect(confirm).toBeDisabled()
  await page.getByLabel(/reason/i).fill('Closing early for an end-to-end test.')
  await expect(confirm).toBeEnabled()
  await confirm.click()

  await expect(card.getByText('Closed').first()).toBeVisible()
  await expect(card.getByRole('button', { name: /^close$/i })).toHaveCount(0)

  await page.reload()
  const reloadedCard = await loadUntilShown(page, market.question)
  await expect(reloadedCard.getByText('Closed').first()).toBeVisible()
  await expect(reloadedCard.getByRole('button', { name: /^close$/i })).toHaveCount(0)

  const trader = await registerTrader('cm_trader')
  await expect(buy(trader, market, 'Yes', '1')).rejects.toThrow()
})
