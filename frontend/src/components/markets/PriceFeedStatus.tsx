import type { ConnectionStatus } from '../../hooks/usePriceSocket'

interface PriceFeedStatusProps {
  status: ConnectionStatus
  /** Margin and the like from the page placing it; it owns no outer spacing. */
  className?: string
}

// What the price feed is doing, when it is doing something a trader should
// know about ([X-4] #37 AC 6).
//
// `connecting` and `connected` say nothing: a feed that is working is the
// normal case and a badge for it would be noise on every screen. The two that
// show are the ones where a price on screen may not be the market's — and
// both keep the last known price visible rather than blanking it, so without
// this the screen would look as authoritative as a live one.
//
// Shared by the detail page and the browse list, which both have a socket to
// report on ([X-1] #444).
export default function PriceFeedStatus({ status, className = '' }: PriceFeedStatusProps) {
  if (status !== 'reconnecting' && status !== 'degraded') return null

  const isDegraded = status === 'degraded'
  return (
    <div
      role="status"
      className={`flex items-center gap-2 text-xs font-medium ${isDegraded ? 'text-danger' : 'text-warning-strong'} ${className}`}
    >
      {/* The dot pulses only while there is something still to wait for. */}
      <span className={`h-2 w-2 shrink-0 rounded-full ${isDegraded ? 'bg-danger' : 'animate-pulse bg-warning'}`} />
      {isDegraded ? 'Live prices unavailable.' : 'Reconnecting to live prices…'}
    </div>
  )
}
