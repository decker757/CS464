import { useEffect, useRef, useState } from 'react'

const WS_BASE = import.meta.env.VITE_REALTIME_WS_URL ?? 'ws://localhost:8004'

export interface OutcomePrice {
  outcome_id: string
  position: number
  price: string
}

export function useMarketPrices(marketId: string | null): OutcomePrice[] | null {
  const [prices, setPrices] = useState<OutcomePrice[] | null>(null)
  const [reconnectCount, setReconnectCount] = useState(0)
  const stateVersionRef = useRef(-1)

  useEffect(() => {
    if (!marketId) return

    stateVersionRef.current = -1
    setPrices(null)

    const ws = new WebSocket(`${WS_BASE}/ws/prices`)

    ws.onopen = () => {
      ws.send(JSON.stringify({ action: 'subscribe', market_id: marketId }))
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string)
        if (msg.type === 'subscribed') {
          // Ack received: market is subscribed. [] distinguishes "subscribed,
          // no trades yet" from null ("not yet acked or connection lost").
          setPrices(prev => prev ?? [])
          return
        }
        if (msg.type !== 'price') return
        if (msg.state_version <= stateVersionRef.current) return
        stateVersionRef.current = msg.state_version
        const sorted = [...(msg.prices as OutcomePrice[])].sort(
          (a, b) => a.position - b.position,
        )
        setPrices(sorted)
      } catch {
        // ignore malformed frames
      }
    }

    ws.onclose = (event) => {
      // Reset so the UI shows no data rather than freezing on stale prices.
      setPrices(null)
      // Reconnect on ordinary server-side close (e.g. deploy restart).
      // Auth expiry (4408) and "too far behind" (4409) also reconnect; the
      // snapshot endpoint (not yet built) is needed for a full recovery after
      // 4409. 4403 (origin denied) is the only code that makes reconnecting
      // pointless.
      if (event.code !== 4403) {
        setReconnectCount(c => c + 1)
      }
    }

    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'unsubscribe', market_id: marketId }))
      }
      ws.close()
    }
  }, [marketId, reconnectCount])

  return prices
}
