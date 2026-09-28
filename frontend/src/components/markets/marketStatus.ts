import type { MarketSummaryOut, PublicMarketSummary } from '../../api/marketApi'
import type { Tone } from '../ui/Badge'

export type MarketStatus = PublicMarketSummary['status']

// The admin's own markets can also be in draft or submitted, which a trader
// never sees (market-service.md's six statuses).
export type AdminMarketStatus = MarketSummaryOut['status']

export const STATUS_CONFIG: Record<AdminMarketStatus, { label: string; tone: Tone }> = {
  draft:              { label: 'Draft',    tone: 'neutral' },
  submitted:          { label: 'Submitted', tone: 'warning' },
  open:               { label: 'Open',    tone: 'success' },
  closed:             { label: 'Closed',  tone: 'neutral' },
  pending_resolution: { label: 'Pending', tone: 'warning' },
  approved:           { label: 'Settled', tone: 'info' },
}

// The label and tone for any status string. A status the frontend does not
// know yet (a new one from the backend, like #12's `settled`) is shown as its
// own name rather than crashing the page.
export function statusDisplay(status: string): { label: string; tone: Tone } {
  if (Object.hasOwn(STATUS_CONFIG, status)) return STATUS_CONFIG[status as AdminMarketStatus]
  return { label: status.replaceAll('_', ' '), tone: 'neutral' }
}

// Trading has stopped once the status leaves "open" OR the close time passes.
// Checking only the clock misses an early close: an admin can close a market
// before its close_time, and close_time stays in the future (market-service.md).
// Takes MarketStatus | AdminMarketStatus: the admin's own markets can also be
// draft or submitted, and neither is "open" either.
export function tradingStopped(status: MarketStatus | AdminMarketStatus, closeTime: string, now = new Date()): boolean {
  return status !== 'open' || new Date(closeTime) <= now
}

// "Closes {date}" only while the market can still trade, so a market closed
// early never shows a future date. Only a draft can have no close time yet.
export function formatCloseTime(status: MarketStatus | AdminMarketStatus, closeTime: string | null): string {
  if (!closeTime) return '—'
  if (tradingStopped(status, closeTime)) return 'Closed'
  const closes = new Date(closeTime).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric' })
  return `Closes ${closes}`
}
