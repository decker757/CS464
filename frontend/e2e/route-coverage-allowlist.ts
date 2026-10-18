import type { AllowedRoute } from './support/routeCoverage'

// [F-12] #341. Routes that deliberately have no end-to-end test.
//
// Every other route in `src/App.tsx` must be opened by at least one Playwright
// test or the run fails. Adding a line here is a decision, not a TODO: say why
// the page does not need a browser, next to the path. An entry whose route
// does get visited fails the run too, so this list cannot quietly rot.

export const ALLOWED_ROUTES: AllowedRoute[] = [
  {
    path: '/',
    reason:
      'The landing page is guests-only navigation: a heading and the links to ' +
      '/login and /register, both of which are covered. It owns no data and ' +
      'calls no service, so a browser adds nothing a unit test does not cover.',
  },
]
