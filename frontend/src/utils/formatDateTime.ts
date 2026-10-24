const MINUTE_MS = 60 * 1000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS
// Past a week "9d ago" says less than a date does, and keeps saying less the
// older it gets, so the date takes over there.
const RELATIVE_LIMIT_MS = 7 * DAY_MS

/** A date and time together, the one format used everywhere a timestamp is
 * shown with both: "18 Oct 2026, 02:30 pm". */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('en-SG', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

/**
 * How long ago `iso` was, to one unit — "just now", "12m ago", "5h ago",
 * "3d ago" — degrading to `formatDateTime` once it is more than a week old.
 * Both forms are read off the browser's own clock, so they land in the
 * reader's time zone without being told which one that is.
 *
 * `now` is a parameter so a caller that already keeps a clock can re-render
 * this on its own tick, and so a test needs no system clock — the same reason
 * `formatCloseCountdown` takes one.
 *
 * Null for a timestamp that does not parse: a caller shows nothing rather
 * than "NaNd ago" (#382 made the same choice for an unparseable close time).
 * A timestamp in the future reads "just now" — a browser clock a few seconds
 * behind the server's is the ordinary cause and does not deserve its own
 * wording.
 */
export function formatTimeAgo(iso: string, now: Date = new Date()): string | null {
  const happenedAt = new Date(iso).getTime()
  if (Number.isNaN(happenedAt)) return null
  const elapsedMs = now.getTime() - happenedAt
  if (elapsedMs >= RELATIVE_LIMIT_MS) return formatDateTime(iso)
  if (elapsedMs < MINUTE_MS) return 'just now'
  if (elapsedMs < HOUR_MS) return `${Math.floor(elapsedMs / MINUTE_MS)}m ago`
  if (elapsedMs < DAY_MS) return `${Math.floor(elapsedMs / HOUR_MS)}h ago`
  return `${Math.floor(elapsedMs / DAY_MS)}d ago`
}
