import { readFileSync } from 'node:fs'
import { ALLOWED_ROUTES } from '../route-coverage-allowlist'
import { findUncoveredRoutes, parseDeclaredRoutes } from './routeCoverage'
import { readVisits } from './visitedRoutes'

// [F-12] #341. After the suite, the check: did a browser open every page?
//
// Only when `CI` is set. Running one file locally opens a handful of routes
// and would otherwise fail on all the others; `CI=1 npm run e2e` runs the
// whole suite and the check with it.

const APP = 'src/App.tsx'

export default function checkRouteCoverage(): void {
  if (!process.env.CI) return

  const declared = parseDeclaredRoutes(readFileSync(APP, 'utf8'))
  const { unvisited, staleAllowlist } = findUncoveredRoutes({
    declared,
    visited: readVisits(),
    allowlist: ALLOWED_ROUTES,
  })

  const problems: string[] = []
  if (unvisited.length > 0) {
    problems.push(
      `No end-to-end test opens these ${unvisited.length} page(s):\n` +
        unvisited.map(route => `  ${route}`).join('\n') +
        '\n\nWrite a test that reaches one, or — if it genuinely does not need a\n' +
        'browser — add it to e2e/route-coverage-allowlist.ts with the reason.',
    )
  }
  if (staleAllowlist.length > 0) {
    problems.push(
      `These routes are in e2e/route-coverage-allowlist.ts but a test did open them:\n` +
        staleAllowlist.map(route => `  ${route}`).join('\n') +
        '\n\nDelete those entries; they are covered now.',
    )
  }
  if (problems.length > 0) throw new Error(`\nRoute coverage (#341)\n\n${problems.join('\n\n')}\n`)
}
