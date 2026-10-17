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
 * and a database earlier runs have filled holds more than one page. Call it
 * right after opening the page, or after `selectTab`, never after a bare tab
 * click (see `selectTab`).
 */
export async function loadUntilShown(page: Page, question: string): Promise<Locator> {
  const card = marketCard(page, question)
  const loadMore = page.getByRole('button', { name: 'Load more' })
  // While a page is on its way the button reads "Loading…", so "Load more"
  // is briefly absent; wait it out rather than read that as the last page.
  const loadingMore = page.getByRole('button', { name: 'Loading…' })
  // On a fresh page the tabs render in the same pass as "Loading markets…", so
  // once they are visible the first page is loading or loaded. After a tab
  // change, selectTab has already waited for that tab's first page to answer.
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

/**
 * Clicks a status tab on the admin markets list and waits for the answer to the
 * first-page request it sends. For a moment after the click the old tab's rows,
 * Load more included, are still on screen, and a Load more clicked then belongs
 * to the old list. The new tab's request is sent by the same effect that
 * resets its list to "Loading markets…" (usePagedMarkets). That render is
 * queued before the request leaves, and the answer needs a round trip, so by
 * the time it has answered the old rows are gone and what loadUntilShown
 * reads is the new tab's list. A timing argument, not a strict ordering, but
 * one with a network round trip of margin.
 */
export async function selectTab(page: Page, name: RegExp): Promise<void> {
  const answered = page.waitForResponse(response => new URL(response.url()).pathname === '/markets/overview')
  await page.getByRole('tab', { name }).click()
  await answered
}
