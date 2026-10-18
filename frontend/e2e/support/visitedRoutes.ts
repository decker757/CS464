import { appendFileSync, existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs'

// [F-12] #341. Where the workers write the pages they opened, and where the
// teardown reads them back. One file of one pathname per line: the workers are
// separate processes, so they cannot share a variable, and a line this short
// appends atomically.

const FILE = 'test-results/visited-routes.txt'

/** Appends one pathname, from whichever worker opened it. */
export function recordVisit(pathname: string): void {
  mkdirSync('test-results', { recursive: true })
  appendFileSync(FILE, `${pathname}\n`)
}

/** Every pathname this run opened. Empty before the first navigation. */
export function readVisits(): string[] {
  if (!existsSync(FILE)) return []
  return readFileSync(FILE, 'utf8').split('\n').filter(Boolean)
}

/**
 * Drops the last run's file, so a page opened yesterday cannot cover a route
 * today. Called once before the suite starts.
 */
export function forgetVisits(): void {
  rmSync(FILE, { force: true })
}
