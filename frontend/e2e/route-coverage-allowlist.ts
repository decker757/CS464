import type { AllowedRoute } from './support/routeCoverage'

// [F-12] #341. Routes that deliberately have no end-to-end test.
//
// Every other route in `src/App.tsx` must be opened by at least one Playwright
// test or the run fails. Adding a line here is a decision, not a TODO: say why
// the page does not need a browser, next to the path. An entry whose route
// does get visited fails the run too, so this list cannot quietly rot.

// Empty since #438: the landing page, the one entry this list held, is now
// opened by responsive-frame.e2e.ts to measure its width on a phone.
export const ALLOWED_ROUTES: AllowedRoute[] = []
