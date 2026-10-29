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

/** Signs out through the account menu, which is where Log Out lives (#437). */
export async function logOut(page: Page, session: Session): Promise<void> {
  // The button carries the signed-in username, so that is its name.
  await page.getByRole('button', { name: session.username }).click()
  await page.getByRole('button', { name: 'Log Out' }).click()
  await expect(page).toHaveURL(/\/login$/)
}

/**
 * The card on a markets list whose question is `question`. Two levels up from
 * the question's text, which is how the admin list and the proposals list
 * both nest it today; a wrapper around the question breaks this.
 *
 * The **last** match, because the browse list shows a market twice once it is
 * in one of the curated bands above the list ([X-1] #448) — once in the band
 * and once in the full list below, which is the point of a band being a
 * shortcut into the list rather than a partition of it. The full list is
 * always the later of the two and always holds every market on the page, so
 * the last match is the one a test can rely on. Every other list shows a
 * market once, where last and only are the same card.
 */
export function marketCard(page: Page, question: string): Locator {
  return page.getByText(question, { exact: true }).locator('xpath=../..').last()
}

/**
 * The browse page's full market list, apart from the curated bands above it
 * ([X-1] #448). Use it to count what the list holds: a market in a band is on
 * screen twice on purpose, so a count across the page is not the list's.
 *
 * Only matches when a band is shown: the list takes its "Browse all" heading
 * only then, so a page with no band has no region to scope to — and needs
 * none, because nothing is on screen twice there.
 */
export function browseList(page: Page): Locator {
  return page.getByRole('region', { name: 'Browse all' })
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

/**
 * The control on a market's page that chooses the outcome the trading panel
 * trades: that outcome's price block (#429). Its name is the label and then
 * the price, which moves, so this matches on the label alone. `label` is
 * matched as written, so it must hold no regular-expression characters.
 */
export function outcomeChoice(page: Page, label: string): Locator {
  return page.getByRole('radiogroup', { name: 'Outcome to trade' }).getByRole('radio', { name: new RegExp(`^${label},`) })
}

/** A screen, and the `<h1>` that says it has been drawn (#438). */
export interface Screen {
  path: string
  heading: string
}

/** How wide the page is drawn (`scrollWidth`) beside how wide the window
 * shows it (`clientWidth`): any more than that scrolls sideways. */
export async function documentWidth(page: Page): Promise<{ scrollWidth: number; clientWidth: number }> {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
}

/**
 * Opens each screen at one viewport size and returns the ones the page
 * scrolls sideways on, with how wide the document came out, so a failure
 * names every offender at once rather than only the first (#438).
 */
export async function overflowingScreens(page: Page, screens: Screen[], viewport: { width: number; height: number }): Promise<string[]> {
  await page.setViewportSize(viewport)
  const overflowing: string[] = []
  for (const screen of screens) {
    await page.goto(screen.path)
    await expect(page.getByRole('heading', { level: 1, name: screen.heading, exact: true })).toBeVisible()
    // On half these screens the heading is drawn in the same pass as a
    // "Loading …" line, before the list or table that might be too wide, so
    // the measurement waits for every such line to go.
    await expect(page.getByText(/^Loading\b/)).toHaveCount(0)
    const { scrollWidth, clientWidth } = await documentWidth(page)
    if (scrollWidth > clientWidth) overflowing.push(`${screen.path} at ${viewport.width}px is ${scrollWidth}px wide`)
  }
  return overflowing
}

/** Where an element is drawn. One that is not drawn at all fails here,
 * rather than as a comparison against nothing. */
export async function boxOf(element: Locator): Promise<{ x: number; y: number; width: number; height: number }> {
  const box = await element.boundingBox()
  if (box === null) throw new Error('the element is not on screen')
  return box
}
