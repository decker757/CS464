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

/**
 * The card for `question` on the admin markets list, clicking Load more until
 * it is shown or there is nothing left to load. The list is paged ([2.1] #210),
 * and a database earlier runs have filled holds more than one page.
 */
export async function loadUntilShown(page: Page, question: string): Promise<Locator> {
  const card = marketCard(page, question)
  const loadMore = page.getByRole('button', { name: 'Load more' })
  // While a page is on its way the button reads "Loading…", so "Load more"
  // is briefly absent; wait it out rather than read that as the last page.
  const loadingMore = page.getByRole('button', { name: 'Loading…' })
  // The tabs render in the same pass as "Loading markets…", so once they are
  // visible the first page is either loading or loaded, never not yet asked for.
  await expect(page.getByRole('tablist')).toBeVisible()
  await expect(page.getByText('Loading markets…')).toHaveCount(0)
  while ((await card.count()) === 0 && (await loadMore.count()) > 0) {
    await loadMore.click()
    await expect(loadingMore).toHaveCount(0)
  }
  return card
}

/**
 * Sets `params` on every request the page sends to `pathname`, on its way out:
 * how a test shrinks the page size, or scopes a list to its own run, while
 * everything else stays real.
 */
export async function setQueryParams(page: Page, pathname: string, params: Record<string, string>): Promise<void> {
  await page.route(
    url => url.pathname === pathname,
    route => {
      const url = new URL(route.request().url())
      for (const [name, value] of Object.entries(params)) url.searchParams.set(name, value)
      return route.continue({ url: url.toString() })
    },
  )
}
