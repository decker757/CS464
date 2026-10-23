import { useCallback, useEffect, useRef, useState } from 'react'
import { getMyOrders, type Order } from '../api/ledgerApi'
import type { OrderFrame } from './useMarketPrices'

export interface MyOrders {
  /** Null until the first read lands. A failed re-read keeps the last list
   *  rather than blanking it, so this goes back to null for nobody. */
  orders: Order[] | null
  /** True once any read has failed. Show it only when `orders` is null:
   *  a re-read that fails after a list is already up has left that list
   *  on screen, and an error over it would say the wrong thing. */
  hasError: boolean
  /** Re-reads the list. Call it after placing or cancelling an order, and
   *  on every socket ack. Does nothing while the hook is disabled. */
  refetch: () => void
  /** Applies one `order` frame from the realtime socket. Pass this as
   *  `useMarketPrices`'s `onOrderFrame` — this hook owns no connection, so a
   *  page holds one socket however many things listen to it. */
  applyFrame: (frame: OrderFrame) => void
}

/**
 * The caller's live limit orders, kept current ([T-6] #26, [T-7] #27).
 *
 * `GET /ledger/orders/me` is unpaged and authoritative, so every read
 * replaces the whole list rather than merging into it (DECISIONS.md,
 * "`GET /ledger/orders/me` returns every live order, unpaged").
 *
 * On top of that, an `order` frame moves `filled_quantity` on the row it
 * names, and a frame that completes an order drops it, since the list holds
 * live orders only. A frame carries no reservation figures, so the caller
 * re-reads the list once it has applied one: the frame is published after
 * the commit that wrote the fill, so that read already shows what the order
 * still holds.
 */
export function useMyOrders(enabled: boolean): MyOrders {
  const [orders, setOrders] = useState<Order[] | null>(null)
  const [hasError, setHasError] = useState(false)

  // The most recent read's own number, so an answer that arrives after a
  // newer one started is dropped instead of overwriting it — the same guard
  // BalanceContext and MarketDetailPage's holdings read both keep.
  const latestReadRef = useRef(0)
  // The highest fill version applied per order id. A frame at or below it is
  // a repeat and is discarded (realtime-service.md, "Order them by
  // fill.state_version"). Two fills of one order are in one market, so their
  // versions are ordered.
  const appliedVersionsRef = useRef(new Map<string, number>())

  // Frames that arrived while a read was in flight. The read answers from
  // before they were published, so replacing the list with it would undo
  // them — they are re-applied on top of the snapshot instead (#393's
  // review). Ordinary frames, arriving with no read outstanding, are applied
  // straight away and never land here.
  const framesDuringReadRef = useRef<OrderFrame[]>([])
  const readInFlightRef = useRef(false)

  // The reducer both paths share: the version check, then the row edit.
  // Returns false for a frame already applied, so a caller knows not to
  // bother the state.
  const reduceFrame = useCallback((frame: OrderFrame): boolean => {
    const applied = appliedVersionsRef.current.get(frame.order_id)
    if (applied !== undefined && frame.fill.state_version <= applied) return false
    appliedVersionsRef.current.set(frame.order_id, frame.fill.state_version)
    setOrders(current => {
      if (current === null) return current
      // A fill that completes the order takes it out of the live list, which
      // is exactly what the next read would show.
      if (frame.status !== 'open') return current.filter(order => order.id !== frame.order_id)
      return current.map(order =>
        order.id === frame.order_id ? { ...order, filled_quantity: frame.filled_quantity } : order,
      )
    })
    return true
  }, [])

  const refetch = useCallback(() => {
    // A market that cannot carry orders costs the ledger nothing, whoever
    // asks for the read.
    if (!enabled) return
    const requestId = ++latestReadRef.current
    readInFlightRef.current = true
    framesDuringReadRef.current = []
    getMyOrders().then(
      result => {
        if (requestId !== latestReadRef.current) return
        readInFlightRef.current = false
        // A fresh list supersedes every frame applied to the old one.
        appliedVersionsRef.current = new Map()
        setOrders(result.orders)
        setHasError(false)
        // Anything published while this was in flight is newer than it.
        const buffered = framesDuringReadRef.current
        framesDuringReadRef.current = []
        for (const frame of buffered) reduceFrame(frame)
      },
      () => {
        if (requestId !== latestReadRef.current) return
        readInFlightRef.current = false
        framesDuringReadRef.current = []
        // The last known list stays on screen, the way BalanceContext keeps
        // the last balance: a failed re-read after a cancel must not look
        // like every order vanished.
        setHasError(true)
      },
    )
  }, [enabled, reduceFrame])

  const applyFrame = useCallback((frame: OrderFrame) => {
    if (readInFlightRef.current) {
      framesDuringReadRef.current.push(frame)
      return
    }
    reduceFrame(frame)
  }, [reduceFrame])

  useEffect(() => {
    refetch()
  }, [refetch])

  return { orders, hasError, refetch, applyFrame }
}
