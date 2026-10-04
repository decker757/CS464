import axios from 'axios'
import { useEffect, useState } from 'react'
import { getMarketSnapshot, type OutcomePrice, type PriceState } from '../api/ledgerApi'

export type { OutcomePrice }

// [X-4] #37 AC 6: the UI displays connection state.
export type ConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'degraded'

const WS_BASE = import.meta.env.VITE_REALTIME_WS_URL ?? 'ws://localhost:8004'
const MAX_RETRY_MS = 30_000

type MarketPriceState = {
  marketId: string
  prices: OutcomePrice[] | null
  status: ConnectionStatus
}

// Current prices and connection status; retains last known prices during reconnect rather than blanking them.
export function useMarketPrices(marketId: string | null): { prices: OutcomePrice[] | null; status: ConnectionStatus } {
  const [state, setState] = useState<MarketPriceState | null>(null)

  useEffect(() => {
    if (!marketId) return

    // Set by the cleanup. Closing the socket ourselves fires onclose too, and
    // without this flag that close would schedule a reconnect of its own.
    let cancelled = false
    let ws: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    // The highest version on screen. Kept across reconnects, because the prices
    // are: a late frame from before the drop must not replace newer ones.
    let version = -1

    // State left by the previous market is not carried over to this one.
    const showPrices = (prices: OutcomePrice[]) => {
      setState(prev => {
        const status = prev?.marketId === marketId ? prev.status : 'connecting'
        return { marketId, prices, status }
      })
    }
    const showStatus = (status: ConnectionStatus) => {
      // A request for a market the page has left may still settle, and must
      // not overwrite the market now on screen.
      if (cancelled) return
      setState(prev => {
        const prices = prev?.marketId === marketId ? prev.prices : null
        return { marketId, prices, status }
      })
    }

    // Snapshot and price frame share a shape, so one function applies both.
    // Anything for another market, or not newer than what is on screen, is
    // dropped: a snapshot can be newer than a frame still in flight, and
    // replicas keep separate counts.
    const apply = (s: PriceState) => {
      if (cancelled || s.market_id !== marketId || s.state_version <= version) return
      version = s.state_version
      showPrices([...s.prices].sort((a, b) => a.position - b.position))
    }

    const loadSnapshot = () => getMarketSnapshot(marketId).then(apply)

    const connect = () => {
      const socket = new WebSocket(`${WS_BASE}/ws/prices`)
      ws = socket

      socket.onopen = () => {
        socket.send(JSON.stringify({ action: 'subscribe', market_id: marketId }))
      }

      socket.onmessage = (event: MessageEvent) => {
        let msg
        try { msg = JSON.parse(event.data as string) } catch { return }
        if (msg.type === 'subscribed') {
          attempt = 0
          // Set here rather than when prices arrive: a reconnect where nothing
          // traded brings a snapshot that `apply` drops as not newer.
          showStatus('connected')
          // Fetched again after the ack: from here on every trade arrives on
          // the socket, and everything earlier is in this snapshot. The
          // version check drops any overlap.
          loadSnapshot().catch(() => {})
        } else if (msg.type === 'price') {
          apply(msg as PriceState)
        }
      }

      socket.onclose = (event: CloseEvent) => {
        if (cancelled) return

        // Origin refused: retrying cannot help. Mark degraded but keep last
        // known prices visible (AC 7).
        if (event.code === 4403) {
          showStatus('degraded')
          return
        }

        // Keep last known prices while reconnecting (AC 7: retain stale prices
        // during temporary loss rather than blanking the display).
        showStatus('reconnecting')

        const delay = Math.min(1000 * 2 ** attempt, MAX_RETRY_MS)
        attempt += 1
        const reconnect = () => {
          if (!cancelled) retryTimer = setTimeout(connect, delay)
        }

        // 4401 / 4408: the access token expired. A browser socket cannot send
        // a fresh one, so refresh the cookie first — any request through
        // `api` does that via its interceptor, and the snapshot is one we need
        // anyway. If even that is refused, the session is over; stop.
        if (event.code === 4401 || event.code === 4408) {
          loadSnapshot().then(reconnect, (err: unknown) => {
            if (axios.isAxiosError(err) && err.response?.status === 401) {
              showStatus('degraded')
              return
            }
            reconnect()
          })
        } else {
          reconnect()
        }
      }
    }

    // The snapshot comes from the ledger, not the socket, so a realtime service
    // that is down or refuses us must not keep a healthy price off the page
    // (AC 1, AC 7). The fetch after `subscribed` is still needed: it covers a
    // trade that lands between this request and the subscription.
    loadSnapshot().catch(() => {})
    connect()

    return () => {
      cancelled = true
      clearTimeout(retryTimer)
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'unsubscribe', market_id: marketId }))
      }
      ws?.close()
    }
  }, [marketId])

  if (!state || state.marketId !== marketId) return { prices: null, status: 'connecting' }
  return { prices: state.prices, status: state.status }
}
