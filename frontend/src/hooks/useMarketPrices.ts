import axios from 'axios'
import { useEffect, useState } from 'react'
import { getMarketSnapshot, type OutcomePrice, type PriceState } from '../api/ledgerApi'

export type { OutcomePrice }

const WS_BASE = import.meta.env.VITE_REALTIME_WS_URL ?? 'ws://localhost:8004'
const MAX_RETRY_MS = 30_000

// The market's current prices: the snapshot when the page opens, then every
// live update. null until the first one arrives, and while reconnecting.
// Follows "The two sequences a client needs" in docs/api/realtime-service.md.
export function useMarketPrices(marketId: string | null): OutcomePrice[] | null {
  // Tagged with the market they belong to, so switching markets shows null
  // straight away rather than the previous market's prices.
  const [state, setState] = useState<{ marketId: string; prices: OutcomePrice[] | null } | null>(null)

  useEffect(() => {
    if (!marketId) return

    // Set by the cleanup. Closing the socket ourselves fires onclose too, and
    // without this flag that close would schedule a reconnect of its own.
    let cancelled = false
    let ws: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let version = -1

    // Snapshot and price frame share a shape, so one function applies both.
    // Anything not newer than what is on screen is dropped: a snapshot can be
    // newer than a frame still in flight, and replicas keep separate counts.
    const apply = (s: PriceState) => {
      if (cancelled || s.state_version <= version) return
      version = s.state_version
      setState({ marketId, prices: [...s.prices].sort((a, b) => a.position - b.position) })
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
          // Fetched after the ack, not before: from here on every trade
          // arrives on the socket, and everything earlier is in the snapshot.
          // The version check drops any overlap.
          loadSnapshot().catch(() => {})
        } else if (msg.type === 'price') {
          apply(msg as PriceState)
        }
      }

      socket.onclose = (event: CloseEvent) => {
        if (cancelled) return
        // Blank the prices rather than leave them frozen, and forget the
        // version so the next snapshot is accepted even if nothing traded.
        version = -1
        setState({ marketId, prices: null })
        // Origin refused: retrying cannot help.
        if (event.code === 4403) return

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
            if (axios.isAxiosError(err) && err.response?.status === 401) return
            reconnect()
          })
        } else {
          reconnect()
        }
      }
    }

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

  return state && state.marketId === marketId ? state.prices : null
}
