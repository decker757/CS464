import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ALLOWED_ROUTES } from '../route-coverage-allowlist'
import { findUncoveredRoutes, parseDeclaredRoutes } from './routeCoverage'

// [F-12] #341. The guard against the guard: `routeCoverage.test.ts` proves the
// comparison on made-up data, and these prove it is wired to the real app. The
// recording half was wrong once already — it watched the `page` fixture and
// missed the two contexts `propose-approve.e2e.ts` opens — and nothing failed.

const E2E = join(import.meta.dirname, '..')
const APP = join(E2E, '../src/App.tsx')

describe('the routes the guard reads', () => {
  it('are the ones App.tsx really declares', () => {
    const declared = parseDeclaredRoutes(readFileSync(APP, 'utf8'))

    // Not a count: that would need editing with every new page, and the
    // parser's own check already compares routes to paths. These three are
    // the shapes that have to keep parsing — the root, a parameter, and the
    // one route whose `path` sits on its own line.
    expect(declared).toContain('/')
    expect(declared).toContain('/markets/:id')
    expect(declared).toContain('/admin/markets/:id/price-history')
  })

  it('does not let the root stand in for every page under it', () => {
    // `/` is the one route the allowlist excuses. If it matched as a prefix,
    // every visit would credit it and no page below it could ever be reported.
    const declared = parseDeclaredRoutes(readFileSync(APP, 'utf8'))

    const { unvisited } = findUncoveredRoutes({
      declared,
      visited: ['/markets', '/portfolio'],
      allowlist: [],
    })

    expect(unvisited).toContain('/')
  })

  it('excuses only routes that still exist', () => {
    const declared = parseDeclaredRoutes(readFileSync(APP, 'utf8'))

    const { unknownAllowlist } = findUncoveredRoutes({
      declared,
      visited: [],
      allowlist: ALLOWED_ROUTES,
    })

    expect(unknownAllowlist).toEqual([])
  })
})

describe('the tests that feed the guard', () => {
  it('all import test from the fixture, so every page they open is recorded', () => {
    // A file importing straight from `@playwright/test` still runs and still
    // passes; its pages simply go unseen, and the routes only it covers get
    // reported as uncovered. The failure is loud but points at the wrong
    // thing, so it is cheaper to catch here.
    const bypassing = readdirSync(E2E)
      .filter(name => name.endsWith('.e2e.ts'))
      .filter(name => /from '@playwright\/test'/.test(readFileSync(join(E2E, name), 'utf8')))

    expect(bypassing).toEqual([])
  })
})
