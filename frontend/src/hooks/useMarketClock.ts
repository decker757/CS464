import { useEffect, useState } from 'react'
import { type AdminMarketStatus, type MarketStatus, tradingStopped } from '../components/markets/marketStatus'

// How often the clock is re-read. The countdown's smallest unit is a minute,
// so ten seconds keeps it honest to within a tick without re-rendering a grid
// of cards every second.
const TICK_MS = 10_000

interface ClockedMarket {
  status: MarketStatus | AdminMarketStatus
  close_time: string | null
}

/**
 * The time to read a market's close countdown against (`formatCloseCountdown`),
 * re-read every ten seconds while the market can still trade.
 *
 * A countdown computed once at render freezes, and a frozen one says a market
 * is still trading after it has closed. Trading stops on the clock, not when
 * the sweep writes CLOSED (ADR 0011), so whatever shows one keeps a clock.
 *
 * A market that has already stopped reads "Closed" for as long as the tab is
 * open, so there is nothing for a clock to change. The last tick before that
 * turns true is the one that writes "Closed", and it stops the clock itself.
 */
export function useMarketClock(market: ClockedMarket): Date {
  const [now, setNow] = useState(() => new Date())
  const hasStopped = tradingStopped(market.status, market.close_time, now)

  useEffect(() => {
    if (hasStopped) return
    const ticker = setInterval(() => setNow(new Date()), TICK_MS)
    return () => clearInterval(ticker)
  }, [hasStopped])

  return now
}
