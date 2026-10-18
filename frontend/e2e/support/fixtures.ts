import { type BrowserContext, test as base } from '@playwright/test'
import { recordVisit } from './visitedRoutes'

// [F-12] #341. The `test` every e2e file imports, instead of the one from
// `@playwright/test`. It is the plain Playwright test with one thing added: it
// writes down every page the test opens, so the teardown can tell which routes
// nothing ever reached. Importing it is the whole opt-in — a new file is
// covered by using it, and one that goes back to `@playwright/test` stops
// contributing, which the guard then reports as an uncovered route.
//
// It listens on the browser rather than on the `page` fixture, because a test
// that needs two people logged in at once opens its own contexts
// (`propose-approve.e2e.ts`), and those pages are not the `page` fixture's.

export { expect } from '@playwright/test'

// Where the app is served. A page pointed at a backend instead — market_service
// answers `GET /markets/{id}` on :8001 — would otherwise credit the route of
// the same name, so only the frontend's own origin counts as visiting a page.
const APP_ORIGIN = new URL(process.env.E2E_BASE_URL ?? 'http://localhost:5173').origin

function recordEveryPageOf(context: BrowserContext): BrowserContext {
  context.on('page', page => {
    page.on('framenavigated', frame => {
      // Only the page itself: an iframe's address is not a route, and
      // `about:blank` is where a page starts rather than somewhere it went.
      if (frame !== page.mainFrame()) return
      const { origin, pathname } = new URL(frame.url())
      if (origin !== APP_ORIGIN) return
      recordVisit(pathname)
    })
  })
  return context
}

export const test = base.extend({
  browser: [
    async ({ browser }, use) => {
      // `newPage()` and the built-in `page` fixture both open their context
      // through this one call, so wrapping it catches every page in the run.
      const openContext = browser.newContext.bind(browser)
      browser.newContext = async options => recordEveryPageOf(await openContext(options))
      await use(browser)
      browser.newContext = openContext
    },
    { scope: 'worker' },
  ],
})
