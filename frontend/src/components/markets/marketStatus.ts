import type { MarketSummaryOut, PublicMarketSummary } from '../../api/marketApi'
import type { Tone } from '../ui/Badge'

export type MarketStatus = PublicMarketSummary['status']

// The admin's own markets can also be in draft or submitted, which a trader
// never sees.
export type AdminMarketStatus = MarketSummaryOut['status']

// Every status either list can show: the admin's two unpublished ones, and
// every published one, `settled` included (market-service.md).
type KnownMarketStatus = AdminMarketStatus | MarketStatus

// `approved` is "approved, waiting for the ledger's payout"; `settled` is final
// ([3.4] #12; the status alone does not prove the ledger paid, ADR 0019).
export const STATUS_CONFIG: Record<KnownMarketStatus, { label: string; tone: Tone }> = {
  draft:              { label: 'Draft',     tone: 'neutral' },
  submitted:          { label: 'Submitted', tone: 'warning' },
  open:               { label: 'Open',      tone: 'success' },
  closed:             { label: 'Closed',    tone: 'neutral' },
  pending_resolution: { label: 'Pending',   tone: 'warning' },
  approved:           { label: 'Approved',  tone: 'info' },
  settled:            { label: 'Settled',   tone: 'neutral' },
}

// The label and tone for any status string. A status the frontend does not
// know yet (a new one from the backend) is shown as its own name rather than
// crashing the page.
export function statusDisplay(status: string): { label: string; tone: Tone } {
  if (Object.hasOwn(STATUS_CONFIG, status)) return STATUS_CONFIG[status as KnownMarketStatus]
  return { label: status.replaceAll('_', ' '), tone: 'neutral' }
}

// Trading has stopped once the status leaves "open" OR the close time passes.
// Checking only the clock misses an early close: an admin can close a market
// before its close_time, and close_time stays in the future (market-service.md).
// Takes MarketStatus | AdminMarketStatus: the admin's own markets can also be
// draft or submitted, and neither is "open" either.
//
// A null close time is a market with no clock, so only the status can have
// stopped it. Pass the null itself: new Date(null) is the 1970 epoch, which
// reads as long past (#382).
export function tradingStopped(status: MarketStatus | AdminMarketStatus, closeTime: string | null, now = new Date()): boolean {
  if (status !== 'open') return true
  if (closeTime === null) return false
  return new Date(closeTime) <= now
}

// "Closes {date}" only while the market can still trade, so a market closed
// early never shows a future date. Only a draft can have no close time yet.
export function formatCloseTime(status: MarketStatus | AdminMarketStatus, closeTime: string | null, now = new Date()): string {
  if (!closeTime) return '—'
  if (tradingStopped(status, closeTime, now)) return 'Closed'
  const closes = new Date(closeTime).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' })
  return `Closes ${closes}`
}

const MINUTE_MS = 60 * 1000
const HOUR_MS = 60 * MINUTE_MS
// Nearer than this and the card counts down; beyond it "in 61h 4m" says
// nothing a date does not, and the card would tick for two days to say it.
const COUNTDOWN_WINDOW_MS = 48 * HOUR_MS

// "3h 12m", "9m", "under a minute". Always rounded down, so the card never
// offers more trading time than there is.
function formatTimeLeft(msLeft: number): string {
  if (msLeft < MINUTE_MS) return 'under a minute'
  const minutes = Math.floor(msLeft / MINUTE_MS)
  if (msLeft < HOUR_MS) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

// What a browse card says about the close: "Closes in 3h 12m" once the close
// is near, and whatever formatCloseTime says otherwise.
//
// The clock is the authority, not the status column (ADR 0011): a market stops
// accepting trades the instant close_time passes, and CLOSED is written a few
// seconds later by the sweep. So on this screen the countdown is the only
// honest answer to "can I still trade this", which is why it goes to zero and
// then reads "Closed" rather than waiting for the status to catch up.
//
// `now` is passed in rather than read here so the caller can re-render it on a
// clock of its own; a value frozen at first render is the bug this fixes.
export function formatCloseCountdown(status: MarketStatus | AdminMarketStatus, closeTime: string | null, now = new Date()): string {
  if (closeTime && !tradingStopped(status, closeTime, now)) {
    const msLeft = new Date(closeTime).getTime() - now.getTime()
    if (msLeft < COUNTDOWN_WINDOW_MS) return `Closes in ${formatTimeLeft(msLeft)}`
  }
  return formatCloseTime(status, closeTime, now)
}
