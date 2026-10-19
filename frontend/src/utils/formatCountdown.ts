const SECOND_MS = 1000
const MINUTE_MS = 60 * SECOND_MS
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS

/**
 * What both screens say about an approved market that has no stored window
 * end. ADR 0023 (section 5) refuses a send-back and a settlement on it — "a
 * null is damage" — so no payout is coming and none is promised.
 */
export const NO_WINDOW_RECORDED = 'No dispute window is recorded for this approved result.'

/** The absolute instant alone — "Tue 11:00" — the one part of the countdown
 * that survives a wrong browser clock (market-service.md's dispute-screen
 * notes, point 1), so it still has to show once the window reads as closed. */
export function formatAbsolute(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleString('en-SG', {
    weekday: 'short', hour: '2-digit', minute: '2-digit', timeZone,
  })
}

// How long is left until `endsAtIso`, to the largest two units — "5h 12m",
// "2d 3h", never finer than minutes, since a browser clock can be wrong by
// more than a second anyway (market-service.md's dispute-screen notes,
// point 1: the absolute time is shown alongside this for that reason).
function formatTimeLeft(ms: number): string {
  if (ms <= 0) return '0m'
  const days = Math.floor(ms / DAY_MS)
  const hours = Math.floor((ms % DAY_MS) / HOUR_MS)
  const minutes = Math.floor((ms % HOUR_MS) / MINUTE_MS)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

/**
 * "Disputes close in 5h 12m (Tue 11:00)" — relative time plus the absolute
 * instant, because the browser's clock can be wrong (market-service.md's
 * dispute-screen notes, point 1). `now` is a parameter so a countdown is
 * reproducible in a test without mocking the system clock.
 */
export function formatCountdown(endsAtIso: string, now: Date = new Date()): string {
  const ms = new Date(endsAtIso).getTime() - now.getTime()
  return `${formatTimeLeft(ms)} (${formatAbsolute(endsAtIso)})`
}

/**
 * Whether a dispute window, as `endsAtIso` names it, is still open — the same
 * display-only check the market detail page and the admin markets list both
 * need to decide between showing the countdown and "Result is final" (and,
 * on the admin list, whether Dispute result can be offered at all). `null`
 * reads as closed: a market with no stored window end has never been
 * approved, or was sent back, either way nothing to dispute right now.
 */
export function disputeWindowOpen(endsAtIso: string | null, now: Date = new Date()): boolean {
  return endsAtIso !== null && new Date(endsAtIso).getTime() > now.getTime()
}
