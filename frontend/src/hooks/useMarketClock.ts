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
 * with a render every ten seconds while the market can still trade. Null while
 * the market is still loading, which keeps no clock.
 *
 * A countdown computed once at render freezes, and a frozen one says a market
 * is still trading after it has closed. Trading stops on the clock, not when
 * the sweep writes CLOSED (ADR 0011), so whatever shows one keeps a clock.
 *
 * The time is read at every render, not kept from the last tick: the tick only
 * has to force a render, like useWindowOpen's. So a render for any other
 * reason is current too — the market page's own timer at close_time, which
 * locks its trading panel, also turns its label to "Closed" in the same
 * render, rather than up to a tick later (#429).
 *
 * A market that has already stopped reads "Closed" for as long as the tab is
 * open, so there is nothing for a clock to change. The last tick before that
 * turns true is the one that writes "Closed", and it stops the clock itself.
 */
export function useMarketClock(market: ClockedMarket | null): Date {
  const now = new Date()
  const [, forceRender] = useState(0)
  const hasStopped = market === null || tradingStopped(market.status, market.close_time, now)

  useEffect(() => {
    if (hasStopped) return
    const ticker = setInterval(() => forceRender(tick => tick + 1), TICK_MS)
    return () => clearInterval(ticker)
  }, [hasStopped])

  return now
}
