import { describe, expect, it } from 'vitest'
import { NOT_FOUND_PROBE, findUncoveredRoutes, parseDeclaredRoutes } from './routeCoverage'

// [F-12] #341. The comparison behind the route-coverage guard, tested on its
// own so the guard itself is proved by something faster than a browser run.

const APP_SOURCE = `
  <Routes>
    <Route path="/" element={<GuestOnly><LandingPage /></GuestOnly>} />
    <Route path="/markets/:id" element={<ProtectedRoute><MarketDetailPage /></ProtectedRoute>} />
    <Route
      path="/admin/markets/:id/price-history"
      element={<ProtectedRoute requireAdmin><MarketPriceHistoryPage /></ProtectedRoute>}
    />
  </Routes>
`

describe('parseDeclaredRoutes', () => {
  it('reads every route, whether its path is on the same line or the next one', () => {
    expect(parseDeclaredRoutes(APP_SOURCE)).toEqual([
      '/',
      '/markets/:id',
      '/admin/markets/:id/price-history',
    ])
  })

  it('refuses a source it found no route in, rather than reporting everything covered', () => {
    expect(() => parseDeclaredRoutes('<div>no routes here</div>')).toThrow(/no routes/i)
  })

  it('refuses a source holding a route whose path it could not read', () => {
    const withUnreadablePath = `
      <Routes>
        <Route path="/markets" element={<MarketsPage />} />
        <Route path={SOME_CONSTANT} element={<OtherPage />} />
      </Routes>
    `
    expect(() => parseDeclaredRoutes(withUnreadablePath)).toThrow(/2 routes.*1 path/i)
  })
})

describe('findUncoveredRoutes', () => {
  const allowNothing: { path: string; reason: string }[] = []

  it('names a declared route that nothing visited', () => {
    const { unvisited } = findUncoveredRoutes({
      declared: ['/markets', '/register'],
      visited: ['/markets'],
      allowlist: allowNothing,
    })
    expect(unvisited).toEqual(['/register'])
  })

  it('counts a visit to a real id against the route that declares the parameter', () => {
    const { unvisited } = findUncoveredRoutes({
      declared: ['/markets/:id'],
      visited: ['/markets/7f3e9c21-0b4a-4d5e-9a1f-2c3b4d5e6f70'],
      allowlist: allowNothing,
    })
    expect(unvisited).toEqual([])
  })

  it('says nothing about a route the allowlist excuses', () => {
    const { unvisited } = findUncoveredRoutes({
      declared: ['/'],
      visited: [],
      allowlist: [{ path: '/', reason: 'guests-only navigation' }],
    })
    expect(unvisited).toEqual([])
  })

  it('reports an allowlisted route that turned out to be visited, so the list cannot rot', () => {
    const { staleAllowlist } = findUncoveredRoutes({
      declared: ['/'],
      visited: ['/'],
      allowlist: [{ path: '/', reason: 'guests-only navigation' }],
    })
    expect(staleAllowlist).toEqual(['/'])
  })

  it('reports an allowlisted route that no longer exists, the other way the list rots', () => {
    const { unknownAllowlist } = findUncoveredRoutes({
      declared: ['/markets'],
      visited: ['/markets'],
      allowlist: [{ path: '/old-page', reason: 'deleted two sprints ago' }],
    })
    expect(unknownAllowlist).toEqual(['/old-page'])
  })

  it('reports a visited page that matches no declared route', () => {
    // The guard reads App.tsx with a regex. Move a block of routes into a
    // child component and it would see fewer routes and call the rest
    // covered; the pages the tests opened then match nothing, which is what
    // says so.
    const { unmatchedVisits } = findUncoveredRoutes({
      declared: ['/markets'],
      visited: ['/markets', '/admin/users'],
      allowlist: allowNothing,
    })
    expect(unmatchedVisits).toEqual(['/admin/users'])
  })

  it('still reports a page only the catch-all matches, and does not let it cover the catch-all', () => {
    // `path="*"` (#355) matches every address there is, so without this the
    // check above could never fire again: a route the parse missed would be
    // credited to the 404 page instead of being reported.
    const { unmatchedVisits, unvisited } = findUncoveredRoutes({
      declared: ['/markets', '*'],
      visited: ['/markets', '/admin/users'],
      allowlist: allowNothing,
    })
    expect(unmatchedVisits).toEqual(['/admin/users'])
    expect(unvisited).toEqual(['*'])
  })

  it('credits the catch-all with the one address a test opens to reach it on purpose', () => {
    const { unmatchedVisits, unvisited } = findUncoveredRoutes({
      declared: ['/markets', '*'],
      visited: ['/markets', NOT_FOUND_PROBE],
      allowlist: allowNothing,
    })
    expect(unmatchedVisits).toEqual([])
    expect(unvisited).toEqual([])
  })

  it('credits a visit to the route that fits it best, not merely one that fits', () => {
    // `/admin/markets/new` is not an `:id`. Asking each route on its own
    // would let the create page cover the market page it has nothing to do
    // with, so the ranking is React Router's.
    const { unvisited } = findUncoveredRoutes({
      declared: ['/admin/markets/new', '/admin/markets/:id'],
      visited: ['/admin/markets/new'],
      allowlist: allowNothing,
    })
    expect(unvisited).toEqual(['/admin/markets/:id'])
  })
})
