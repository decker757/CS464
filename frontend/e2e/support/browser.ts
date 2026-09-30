import { expect, type Locator, type Page } from '@playwright/test'
import { PASSWORD, type Session } from './backend'

/** Logs in through the login form, which sets the session cookies for every service. */
export async function logIn(page: Page, session: Session): Promise<void> {
  await page.goto('/login')
  await page.getByLabel('Username or Email').fill(session.username)
  // Exact, or the show-password toggle matches too.
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByRole('button', { name: /log in/i }).click()
  await expect(page).toHaveURL(/\/markets$/)
}

/**
 * The card on a markets list whose question is `question`. Two levels up from
 * the question's text, which is how the admin list and the proposals list
 * both nest it today; a wrapper around the question breaks this.
 */
export function marketCard(page: Page, question: string): Locator {
  return page.getByText(question, { exact: true }).locator('xpath=../..')
}
