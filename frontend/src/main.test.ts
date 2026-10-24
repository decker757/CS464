import { describe, expect, it } from 'vitest'

// [FE][T-5] #453. recharts roughly doubles the main bundle (App.tsx), so it is
// only ever reached through a dynamic import(). Vite puts everything the entry
// reaches through static imports into the bundle every page load downloads, so
// this walks that same graph in the source and fails on any chain of files
// that reaches recharts — naming the import that pulled it in, which the size
// of a built file never would.

/** Every source file, as text, keyed the way the walk names them:
 * './pages/MarketDetailPage.tsx'. */
const SOURCES = import.meta.glob<string>(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

const ENTRY = './main.tsx'
const DETAIL_PAGE = './pages/MarketDetailPage.tsx'
const CHART = './components/markets/PriceHistoryChart.tsx'

// `import x from 'y'`, `import { a, type B } from 'y'`, `export * from 'y'`
// and the bare `import 'y'`, at the start of a line. Not `import type`, which
// the compiler erases, and not `import('y')`, which is the split this guards.
const STATIC_IMPORT = /^\s*(?:import|export)\s+(?!type\s)(?:[\w*{}\s,$]*?\s*from\s*)?['"]([^'"]+)['"]/gm

const SUFFIXES = ['', '.ts', '.tsx', '/index.ts', '/index.tsx']

interface StaticGraph {
  /** Every source file the start loads without a dynamic import. */
  files: string[]
  /** Each package imported, with the chain of files that first reached it. */
  packages: Map<string, string[]>
}

/** The source file a relative specifier names, or null for anything that is
 * not a source file — a stylesheet, an image. */
function resolveRelative(fromFile: string, specifier: string): string | null {
  const parts = fromFile.split('/').slice(0, -1)
  for (const part of specifier.split('/')) {
    if (part === '..') parts.pop()
    else if (part !== '.') parts.push(part)
  }
  const base = parts.join('/')
  for (const suffix of SUFFIXES) {
    if (`${base}${suffix}` in SOURCES) return `${base}${suffix}`
  }
  return null
}

function staticGraphOf(start: string): StaticGraph {
  const files = [start]
  const packages = new Map<string, string[]>()
  const chains = [[start]]

  while (chains.length > 0) {
    const chain = chains.shift()!
    const file = chain[chain.length - 1]
    for (const match of SOURCES[file].matchAll(STATIC_IMPORT)) {
      const specifier = match[1]
      if (!specifier.startsWith('.')) {
        if (!packages.has(specifier)) packages.set(specifier, chain)
        continue
      }
      const imported = resolveRelative(file, specifier)
      if (imported === null || files.includes(imported)) continue
      files.push(imported)
      chains.push([...chain, imported])
    }
  }
  return { files, packages }
}

/** Each chain of static imports from `start` that ends in recharts. */
function chainsToRecharts(start: string): string[] {
  const found: string[] = []
  for (const [name, chain] of staticGraphOf(start).packages) {
    if (name === 'recharts' || name.startsWith('recharts/')) found.push([...chain, name].join(' → '))
  }
  return found
}

describe('the main bundle', () => {
  it('reaches recharts through no static import from the entry', () => {
    // The walk has to reach the page that shows the chart, and has to see the
    // chart's own import of recharts, or the empty answer below proves nothing.
    expect(staticGraphOf(ENTRY).files).toContain(DETAIL_PAGE)
    expect(chainsToRecharts(CHART)).toEqual([`${CHART} → recharts`])

    expect(chainsToRecharts(ENTRY)).toEqual([])
  })
})
