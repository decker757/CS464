import { useCallback, useEffect, useRef, useState } from 'react'
import { getMyOrders, type Order } from '../api/ledgerApi'
import { compareCredits } from '../utils/formatCredits'
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

  // Frames that arrived while a read was in flight. The read may have been
  // answered before they were published, and replacing the list with it
  // would then undo them, so they are applied again on top of it (#393's
  // review). It may equally have been answered after them; the reducer's
  // only-forwards rule is what makes re-applying those harmless. Ordinary
  // frames, arriving with no read outstanding, are applied straight away and
  // never land here.
  const framesDuringReadRef = useRef<OrderFrame[]>([])
  const readInFlightRef = useRef(false)

  // The reducer both paths share. A frame moves `filled_quantity` forwards
  // only: an order's filled quantity never shrinks, so a frame at or below
  // the row's figure is a repeat or is older than what the row already
  // shows, and is discarded. That is the same order realtime-service.md asks
  // for ("Order them by fill.state_version" — two fills of one order are in
  // one market, so their versions and their filled quantities rise
  // together), but unlike a version it can also be compared with a row a
  // fresh read just delivered, which carries no version at all.
  const reduceFrame = useCallback((frame: OrderFrame) => {
    setOrders(current => {
      if (current === null) return current
      // A fill that completes the order takes it out of the live list, which
      // is exactly what the next read would show.
      if (frame.status !== 'open') return current.filter(order => order.id !== frame.order_id)
      return current.map(order => {
        if (order.id !== frame.order_id) return order
        if (compareCredits(frame.filled_quantity, order.filled_quantity) <= 0) return order
        return { ...order, filled_quantity: frame.filled_quantity }
      })
    })
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
        setOrders(result.orders)
        setHasError(false)
        // Anything published while this was in flight may or may not be in
        // it; the reducer keeps only what moves a row forwards.
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
