import { matchPath } from 'react-router-dom'

// [F-12] #341. The comparison behind the route-coverage guard. Pure, and free
// of node and playwright, so `routeCoverage.test.ts` can run it under vitest;
// reading the files and failing the run is `globalTeardown.ts`'s job.

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

/**
 * The routes no test opened, and the allowlist entries that are no longer true.
 *
 * A visited `/markets/7f3e…` counts for a declared `/markets/:id`: the match is
 * React Router's own, so the guard agrees with the router about what a path is.
 */
export function findUncoveredRoutes({ declared, visited, allowlist }: Coverage): {
  unvisited: string[]
  staleAllowlist: string[]
} {
  const allowed = new Set(allowlist.map(entry => entry.path))
  const wasVisited = (route: string): boolean =>
    visited.some(pathname => matchPath(route, pathname) !== null)

  return {
    unvisited: declared.filter(route => !allowed.has(route) && !wasVisited(route)),
    staleAllowlist: declared.filter(route => allowed.has(route) && wasVisited(route)),
  }
}
