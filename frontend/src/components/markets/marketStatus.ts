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
// `voided` is final too, with no winner: every holder was refunded instead
// (ADR 0025). Both read the same to a trader and an administrator.
export const STATUS_CONFIG: Record<KnownMarketStatus, { label: string; tone: Tone }> = {
  draft:              { label: 'Draft',     tone: 'neutral' },
  submitted:          { label: 'Submitted', tone: 'warning' },
  open:               { label: 'Open',      tone: 'success' },
  closed:             { label: 'Closed',    tone: 'neutral' },
  pending_resolution: { label: 'Pending',   tone: 'warning' },
  approved:           { label: 'Approved',  tone: 'info' },
  settled:            { label: 'Settled',   tone: 'neutral' },
  voided:             { label: 'Voided',    tone: 'neutral' },
}

/** Whose words a status is shown in: an administrator's, or a trader's. */
export type StatusAudience = 'admin' | 'trader'

// What a trader is shown instead, where the status's own name is a step of an
// internal state machine ([X-1] #431). `pending_resolution` and `approved`
// both mean "the result is being decided": an outcome has been proposed, a
// second administrator has to agree with it (ADR 0016), and even an approved
// one goes back to CLOSED if it is sent back inside its dispute window
// (ADR 0023). Neither is final and only `settled` is, so the trader's words
// say what a trader is waiting for and never mention who decides it.
//
// Still one label per status, rather than the single "Resolving" pill #431
// offers: `?status=` on GET /public/markets takes exactly one status
// (market-service.md), so a pill standing for two of them cannot be asked
// for in one request, and an approved market would end up matching no pill
// at all. Collapsing them needs a filter that accepts a group, which is a
// backend ticket.
//
// The administrator's screens keep STATUS_CONFIG's precise words, which is
// what the default audience is: AdminMarketsPage's own test reads the
// "Pending" tab by that name.
const TRADER_LABELS: Partial<Record<KnownMarketStatus, string>> = {
  pending_resolution: 'Resolving',
  approved: 'Awaiting payout',
  settled: 'Resolved',
}

// The label and tone for any status string, in `audience`'s words. A status
// the frontend does not know yet (a new one from the backend) is shown as its
// own name rather than crashing the page.
export function statusDisplay(status: string, audience: StatusAudience = 'admin'): { label: string; tone: Tone } {
  if (!Object.hasOwn(STATUS_CONFIG, status)) return { label: status.replaceAll('_', ' '), tone: 'neutral' }
  const forAdmins = STATUS_CONFIG[status as KnownMarketStatus]
  if (audience === 'admin') return forAdmins
  return { label: TRADER_LABELS[status as KnownMarketStatus] ?? forAdmins.label, tone: forAdmins.tone }
}

// Trading has stopped once the status leaves "open" OR the close time passes.
// Checking only the clock misses an early close: an admin can close a market
// before its close_time, and close_time stays in the future (market-service.md).
// Takes MarketStatus | AdminMarketStatus: the admin's own markets can also be
// draft or submitted, and neither is "open" either.
// A null close time is no clock and reads as stopped, as the backend's
// trading_is_open does; so does a date that does not parse (#382).
export function tradingStopped(status: MarketStatus | AdminMarketStatus, closeTime: string | null, now = new Date()): boolean {
  if (status !== 'open') return true
  if (closeTime === null) return true
  const closes = new Date(closeTime)
  if (Number.isNaN(closes.getTime())) return true
  return closes <= now
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
