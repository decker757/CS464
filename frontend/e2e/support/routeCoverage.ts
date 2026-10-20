import { matchRoutes } from 'react-router-dom'

// [F-12] #341. The comparison behind the route-coverage guard. Pure, and free
// of node and playwright, so `routeCoverage.test.ts` can run it under vitest;
// reading the files and failing the run is `globalTeardown.ts`'s job.

/**
 * The one address a test opens to reach the 404 page on purpose (#355).
 *
 * The catch-all `path="*"` matches every address there is, so a visit only it
 * matches is exactly what `unmatchedVisits` exists to report: a page whose
 * real route the parse missed. Every such visit is still reported, except this
 * one, which is the only visit that credits the catch-all.
 */
export const NOT_FOUND_PROBE = '/e2e-no-such-page'

const CATCH_ALL = '*'

export interface AllowedRoute {
  path: string
  reason: string
}

interface Coverage {
  declared: string[]
  visited: string[]
  allowlist: AllowedRoute[]
}

/**
 * Every path a `<Route>` declares in App.tsx, in the order they appear.
 *
 * Throws if it read no routes at all, or fewer paths than there are routes —
 * a guard that silently matches nothing would report every page covered.
 */
export function parseDeclaredRoutes(appSource: string): string[] {
  // `<Routes>` opens the list and is not one of them, so the name has to be
  // followed by whitespace: `<Route path=` and `<Route\n  path=` both are.
  const declarations = appSource.split(/<Route\s/).slice(1)
  const paths: string[] = []
  for (const declaration of declarations) {
    // Lazily, so it stops at this route's own path rather than running on
    // into the next one. `element=` always follows `path=`, never precedes it.
    const declared = /^[^>]*?\bpath="([^"]+)"/s.exec(declaration)
    if (declared) paths.push(declared[1])
  }

  if (declarations.length === 0) {
    throw new Error('Found no routes in App.tsx. The guard cannot read it any more.')
  }
  if (paths.length !== declarations.length) {
    throw new Error(
      `App.tsx declares ${declarations.length} routes but only ${paths.length} paths could be read. ` +
        'A route whose path is not a plain string needs the guard taught about it.',
    )
  }
  return paths
}

interface Uncovered {
  /** Declared, opened by nobody, and not excused. */
  unvisited: string[]
  /** Excused, but a test opened it after all. */
  staleAllowlist: string[]
  /** Excused, but `App.tsx` no longer declares it. */
  unknownAllowlist: string[]
  /** Opened, but matching no declared route bar the catch-all — so the parse missed something. */
  unmatchedVisits: string[]
}

/**
 * The routes no test opened, plus the three ways this answer can go stale.
 *
 * A visited `/markets/7f3e…` counts for a declared `/markets/:id`, and the
 * ranking is React Router's own `matchRoutes`, so each visit credits the route
 * that fits it best rather than every route it merely fits: `/admin/markets/new`
 * is the create page, not an `:id`.
 *
 * `unmatchedVisits` is what stops the regex in `parseDeclaredRoutes` failing
 * quietly. Move a block of routes into a child component and this reads fewer
 * routes and calls the rest covered — but the pages the tests opened then match
 * nothing, and that says so.
 */
export function findUncoveredRoutes({ declared, visited, allowlist }: Coverage): Uncovered {
  const routes = declared.map(path => ({ path }))
  const reached = new Set<string>()
  const unmatchedVisits: string[] = []
  for (const pathname of visited) {
    const matched = matchRoutes(routes, pathname)
    const route = matched?.at(-1)?.route.path
    const isStrayCatchAll = route === CATCH_ALL && pathname !== NOT_FOUND_PROBE
    if (route === undefined || isStrayCatchAll) {
      if (!unmatchedVisits.includes(pathname)) unmatchedVisits.push(pathname)
      continue
    }
    reached.add(route)
  }

  const allowed = new Set(allowlist.map(entry => entry.path))
  const isDeclared = new Set(declared)
  return {
    unvisited: declared.filter(route => !allowed.has(route) && !reached.has(route)),
    staleAllowlist: declared.filter(route => allowed.has(route) && reached.has(route)),
    unknownAllowlist: allowlist.map(entry => entry.path).filter(path => !isDeclared.has(path)),
    unmatchedVisits,
  }
}
