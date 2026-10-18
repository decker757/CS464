import { useEffect, useState } from 'react'
import { disputeWindowOpen } from '../utils/formatCountdown'

// setTimeout overflows a 32-bit int at ~24.8 days; a window further out than
// that gets no timer, since the page will have refetched long before then
// (MarketDetailPage's close_time timer uses the same guard).
const MAX_TIMEOUT_MS = 2 ** 31 - 1

/**
 * Whether a dispute window is still open, re-evaluated the instant it closes
 * rather than only at render time ([3.3] #11). Without this, a countdown or
 * a Dispute result control stays stale until something else causes a
 * re-render — on the admin markets list that can be hours, since an approved
 * market has no trades to force one.
 *
 * `endsAtIso` is display-only input: the server still decides on its own
 * clock (market-service.md's dispute-screen notes, point 8), so this exists
 * to retire a stale "still open" reading, never to grant one.
 */
export function useWindowOpen(endsAtIso: string | null): boolean {
  // Recomputed every render, the same as a plain derived value — the timer
  // below only has to force a re-render at the instant the window closes,
  // never to hold the answer itself, so there is nothing to reset when
  // endsAtIso changes to a different value.
  const open = disputeWindowOpen(endsAtIso)
  const [, forceRender] = useState(0)

  useEffect(() => {
    if (endsAtIso === null) return
    const ms = new Date(endsAtIso).getTime() - Date.now()
    if (ms > MAX_TIMEOUT_MS || ms <= 0) return
    const timer = setTimeout(() => forceRender(tick => tick + 1), ms)
    return () => clearTimeout(timer)
  }, [endsAtIso])

  return open
}
