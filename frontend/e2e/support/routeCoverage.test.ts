import { describe, expect, it } from 'vitest'
import { findUncoveredRoutes, parseDeclaredRoutes } from './routeCoverage'

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
})
