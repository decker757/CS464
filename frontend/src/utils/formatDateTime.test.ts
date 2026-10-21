import { describe, expect, it } from 'vitest'
import { formatDateTime, formatTimeAgo } from './formatDateTime'

const NOW = new Date('2026-10-18T12:00:00Z')
const MINUTE_MS = 60 * 1000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS

/** The instant `ms` before NOW, as the ledger would send it. */
function msBeforeNow(ms: number): string {
  return new Date(NOW.getTime() - ms).toISOString()
}

describe('formatTimeAgo', () => {
  // [X-1] #445 AC 4: a card says when the book last moved, and the answer has
  // to stay useful whether that was a minute, an afternoon or a fortnight ago.
  // Each row crosses one of the thresholds, so a unit that stopped coarsening
  // fails here.
  it('coarsens the unit as the timestamp ages', () => {
    expect(formatTimeAgo(msBeforeNow(20 * 1000), NOW)).toBe('just now')
    expect(formatTimeAgo(msBeforeNow(12 * MINUTE_MS), NOW)).toBe('12m ago')
    expect(formatTimeAgo(msBeforeNow(5 * HOUR_MS), NOW)).toBe('5h ago')
    expect(formatTimeAgo(msBeforeNow(3 * DAY_MS), NOW)).toBe('3d ago')
  })

  // Past a week a count of days says less than the date does, so it gives way
  // to the one format the rest of the app shows a timestamp in.
  it('falls back to the absolute date once it is more than a week old', () => {
    const old = msBeforeNow(9 * DAY_MS)
    expect(formatTimeAgo(old, NOW)).toBe(formatDateTime(old))
  })

  // A browser clock a little behind the server's puts occurred_at in the
  // future. "in -3m" or a negative count would be worse than the truth.
  it('reads just now for a timestamp in the future', () => {
    expect(formatTimeAgo(new Date(NOW.getTime() + 30 * 1000).toISOString(), NOW)).toBe('just now')
  })

  // #382 hit the same thing on a close time: a date that does not parse must
  // not reach the screen as "NaNd ago". Null is the caller's cue to say nothing.
  it('is null for a timestamp that does not parse', () => {
    expect(formatTimeAgo('not a date', NOW)).toBeNull()
  })
})
