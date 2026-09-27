import type { PublicMarketSummary } from '../../api/marketApi'
import type { Tone } from '../ui/Badge'

export type MarketStatus = PublicMarketSummary['status']

export const STATUS_CONFIG: Record<MarketStatus, { label: string; tone: Tone }> = {
  open:               { label: 'Open',    tone: 'success' },
  closed:             { label: 'Closed',  tone: 'neutral' },
  pending_resolution: { label: 'Pending', tone: 'warning' },
  approved:           { label: 'Settled', tone: 'info' },
}

// The label and tone for any status string. A status the frontend does not
// know yet (a new one from the backend, like #12's `settled`) is shown as its
// own name rather than crashing the page.
export function statusDisplay(status: string): { label: string; tone: Tone } {
  if (Object.hasOwn(STATUS_CONFIG, status)) return STATUS_CONFIG[status as MarketStatus]
  return { label: status.replaceAll('_', ' '), tone: 'neutral' }
}

// Trading has stopped once the status leaves "open" OR the close time passes.
// Checking only the clock misses an early close: an admin can close a market
// before its close_time, and close_time stays in the future (market-service.md).
export function tradingStopped(status: MarketStatus, closeTime: string, now = new Date()): boolean {
  return status !== 'open' || new Date(closeTime) <= now
}
