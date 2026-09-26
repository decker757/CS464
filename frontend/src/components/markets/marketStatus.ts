import type { PublicMarketSummary } from '../../api/marketApi'

export type MarketStatus = PublicMarketSummary['status']

export const STATUS_CONFIG: Record<MarketStatus, { label: string; color: string; bg: string; border: string }> = {
  open:               { label: 'Open',    color: '#16a34a', bg: 'rgba(22,163,74,0.1)',   border: 'rgba(22,163,74,0.25)' },
  closed:             { label: 'Closed',  color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.25)' },
  pending_resolution: { label: 'Pending', color: '#d97706', bg: 'rgba(217,119,6,0.1)',   border: 'rgba(217,119,6,0.25)' },
  approved:           { label: 'Settled', color: '#2563eb', bg: 'rgba(37,99,235,0.1)',   border: 'rgba(37,99,235,0.25)' },
}

// Trading has stopped once the status leaves "open" OR the close time passes.
// Checking only the clock misses an early close: an admin can close a market
// before its close_time, and close_time stays in the future (market-service.md).
export function tradingStopped(status: MarketStatus, closeTime: string, now = new Date()): boolean {
  return status !== 'open' || new Date(closeTime) <= now
}
