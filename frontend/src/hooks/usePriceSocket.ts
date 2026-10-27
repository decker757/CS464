import axios from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { PriceState } from '../api/ledgerApi'

// [X-4] #37 AC 6: the UI displays connection state.
export type ConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'degraded'

const WS_BASE = import.meta.env.VITE_REALTIME_WS_URL ?? 'ws://localhost:8004'
const MAX_RETRY_MS = 30_000

/** One `order` frame: a fill of one of the caller's resting orders
 *  (docs/api/realtime-service.md, "Order updates"). */
export interface OrderFrame {
  type: 'order'
  order_id: string
  market_id: string
  status: 'open' | 'filled' | 'cancelled' | 'released'
  quantity: string
  filled_quantity: string
  fill: { state_version: number }
}

export interface PriceSocketOptions {
  /**
   * Called for each `order` frame this connection receives.
   *
   * Order frames need no subscribe: a socket gets the frames of the user its
   * token names, whatever markets it follows. They ride this connection
   * rather than one of their own so a page holds a single socket, which is
   * also what keeps "the" socket unambiguous in a test.
   */
  onOrderFrame?: (frame: OrderFrame) => void
  /**
   * Called with the markets this socket has just subscribed to, the first ack
   * included, once the relay has answered every subscribe outstanding — so a
   * burst of acks arrives here as one call. Frames published before an ack
   * never reached this socket —
   * while a dropped connection was away, or between the caller's own read and
   * the ack — so a caller that tracks state re-reads those markets here
   * ([X-4] #37's recovery path, realtime-service.md "Reconnecting").
   */
  onSubscribed?: (marketIds: string[]) => void
  /**
   * Called with the markets this connection was refused a subscription for,
   * because it is at `MAX_SUBSCRIPTIONS_PER_CONNECTION`.
   *
   * The refusal frame names no market — it is `{code, message}` and nothing
   * else (realtime-service.md) — so what it reports is the subscribe this
   * hook had outstanding when the refusal arrived. The ceiling and the browse
   * page size are configured in different services and can drift, so a caller
   * treats this as "these markets will not be pushed to" and falls back to
   * reading them, rather than trusting any particular number.
   */
  onRefused?: (marketIds: string[]) => void
  /**
   * Called when a new connection starts subscribing from scratch, so a caller
   * holding refusals from the old one can forget them.
   *
   * A refusal belongs to the connection that gave it: the ceiling counts a
   * connection's own subscriptions, and a reconnect holds none of the last
   * one's. Without this a market refused once is treated as never-pushed-to
   * for the life of the page, so it keeps being polled while the new
   * connection pushes its frames.
   */
  onConnectionReset?: () => void
  /**
   * Read whatever this caller shows, as the hook mounts, and hand each answer
   * to `apply`.
   *
   * The read belongs to the caller — one market's snapshot on the detail page,
   * a page of them in one request on the browse list — and the moment belongs
   * here, so neither has to know how the other reads.
   */
  onMount?: (apply: (snapshot: PriceState) => void, marketIds: string[]) => void
  /**
   * The same read, after a `subscribed` ack, for the markets just acked —
   * all of them in one call once the relay has answered every subscribe
   * outstanding, so a page of acks is one read and not one per market.
   *
   * Frames published before an ack never reached this socket — while a dropped
   * connection was away, or between the caller's own read and the ack — so
   * this is the recovery path ([X-4] #37, realtime-service.md "Reconnecting").
   */
  onAck?: (apply: (snapshot: PriceState) => void, marketIds: string[]) => void
  /**
   * One request through `api`, to refresh the session cookie before a
   * reconnect, when the socket closed because the access token expired.
   *
   * A browser socket cannot send a fresh token, so something has to go through
   * the axios interceptor first. Rejecting with a 401 means the session is
   * over and the hook stops; any other rejection reconnects anyway.
   */
  onSessionRefresh?: () => Promise<PriceState>
}

/** A frame carrying a price, already narrowed from the socket's JSON. */
type PriceFrame = PriceState & { type: 'price' }

// The socket, its subscriptions and its reconnect: the transport half of live
// prices, with no opinion about what is rendered.
//
// It follows a *set* of markets, because the browse list follows every card it
// renders ([X-1] #444) and the detail page follows one. Both go through here
// rather than each owning a socket: the reconnect, the backoff, the degraded
// states and the version check are the hard part, and two copies of them
// would be two sets of answers about a dropped connection.
//
// `marketIds` is read as a set of ids, so a caller may pass a fresh array each
// render. Adding an id subscribes it; removing one unsubscribes it; neither
// rebuilds the connection.
export function usePriceSocket(
  marketIds: string[],
  { onOrderFrame, onSubscribed, onRefused, onConnectionReset, onMount, onAck, onSessionRefresh }: PriceSocketOptions = {},
): {
  snapshotsByMarket: Map<string, PriceState>
  status: ConnectionStatus
  /**
   * Render a snapshot this hook did not receive — a caller's own read of the
   * ledger, on mount or after an ack.
   *
   * Callers go through here rather than keeping their own copy so there is one
   * high-water mark per market: two of them disagree the moment a frame and a
   * snapshot cross, and the older answer wins on whichever side it landed.
   */
  applySnapshot: (snapshot: PriceState) => void
} {
  const [snapshotsByMarket, setSnapshotsByMarket] = useState<Map<string, PriceState>>(new Map())
  const [status, setStatus] = useState<ConnectionStatus>('connecting')

  // Read through refs inside the socket's own callbacks, so a caller passing a
  // fresh closure each render does not tear the connection down and rebuild it.
  const onOrderFrameRef = useRef(onOrderFrame)
  const onSubscribedRef = useRef(onSubscribed)
  const onRefusedRef = useRef(onRefused)
  const onConnectionResetRef = useRef(onConnectionReset)
  const onMountRef = useRef(onMount)
  const onAckRef = useRef(onAck)
  const onSessionRefreshRef = useRef(onSessionRefresh)
  useEffect(() => {
    onOrderFrameRef.current = onOrderFrame
    onSubscribedRef.current = onSubscribed
    onRefusedRef.current = onRefused
    onConnectionResetRef.current = onConnectionReset
    onMountRef.current = onMount
    onAckRef.current = onAck
    onSessionRefreshRef.current = onSessionRefresh
  })

  // What the caller wants followed, for the effect's own callbacks to read
  // without the connection depending on the array's identity.
  const wantedRef = useRef<string[]>(marketIds)
  wantedRef.current = marketIds

  // The live socket, so the subscription effect below can send on the
  // connection the connection effect owns.
  const socketRef = useRef<WebSocket | null>(null)
  // The current connection's `apply`, for `applySnapshot` below. It closes
  // over that connection's version map and its cancellation, so a snapshot a
  // caller read is held to the same check a frame is.
  const applyRef = useRef<(snapshot: PriceState) => void>(() => {})
  // What this connection currently believes it is subscribed to. Cleared on a
  // drop: a new connection carries none of the old one's subscriptions.
  const subscribedRef = useRef(new Set<string>())
  // Subscribes sent and not yet answered, oldest first. A `too_many_subscriptions`
  // names no market, so the oldest outstanding subscribe is the one it refused.
  const pendingRef = useRef<string[]>([])
  // Markets acked since the caller was last told, handed over together once
  // nothing is pending: a page's acks arrive one frame per market, and a read
  // per ack would be a request per card.
  const ackedRef = useRef<string[]>([])
  // The current connection's reconciliation, for the followed-set effect below.
  const reconcileRef = useRef<() => void>(() => {})
  // Markets the caller has already been asked to read. The socket's own reads
  // on an ack are separate; this is only the first read of a market.
  const readRef = useRef(new Set<string>())

  // One connection for the life of the hook, not one per market: the effect
  // depends on nothing, so changing the followed set never reconnects.
  useEffect(() => {
    // Set by the cleanup. Closing the socket ourselves fires onclose too, and
    // without this flag that close would schedule a reconnect of its own.
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0

    // The highest version rendered per market. Kept across reconnects, because
    // the prices are: a late frame from before a drop must not replace a newer
    // one, and each replica keeps its own count (realtime-service.md).
    const versions = new Map<string, number>()

    const apply = (frame: PriceState) => {
      if (cancelled) return
      const seen = versions.get(frame.market_id)
      if (seen !== undefined && frame.state_version <= seen) return
      versions.set(frame.market_id, frame.state_version)
      const sorted = [...frame.prices].sort((a, b) => a.position - b.position)
      setSnapshotsByMarket(previous => new Map(previous).set(frame.market_id, { ...frame, prices: sorted }))
    }
    applyRef.current = apply

    // Bring the connection's subscriptions in line with what the caller wants.
    // Safe to call at any time: before the socket is open it does nothing, and
    // `onopen` calls it again, so a followed set that arrived while the socket
    // was still connecting is not lost. That is the normal case on the detail
    // page, whose market id is null until the market itself has loaded.
    const reconcile = () => {
      const socket = socketRef.current
      if (!socket || socket.readyState !== WebSocket.OPEN) return

      // A card that has left the page stops being paid for: the ceiling counts
      // markets, so an unsubscribe is what makes room for the next page. The
      // relay answers commands in order, so these go first — sent after the
      // subscribes, they would free the room only once every new subscribe
      // had already been refused.
      const wanted = new Set(wantedRef.current)
      for (const id of [...subscribedRef.current]) {
        if (wanted.has(id)) continue
        subscribedRef.current.delete(id)
        socket.send(JSON.stringify({ action: 'unsubscribe', market_id: id }))
      }
      for (const id of wantedRef.current) {
        if (subscribedRef.current.has(id)) continue
        subscribedRef.current.add(id)
        pendingRef.current.push(id)
        socket.send(JSON.stringify({ action: 'subscribe', market_id: id }))
      }
    }
    reconcileRef.current = reconcile

    // Tell the caller about the markets acked since it was last told, once the
    // relay has answered every subscribe this connection has outstanding.
    const reportAcked = () => {
      if (pendingRef.current.length > 0 || ackedRef.current.length === 0) return
      const acked = ackedRef.current
      ackedRef.current = []
      onAckRef.current?.(apply, acked)
      onSubscribedRef.current?.(acked)
    }

    const connect = () => {
      const socket = new WebSocket(`${WS_BASE}/ws/prices`)
      socketRef.current = socket

      socket.onopen = () => {
        // A reconnect re-subscribes from scratch: the new connection holds
        // none of the old one's subscriptions, and the ack is what triggers
        // the refetch that covers everything published while we were away.
        // The old connection's refusals go with its subscriptions — the
        // ceiling is per connection, so this one may well have room.
        subscribedRef.current = new Set()
        pendingRef.current = []
        ackedRef.current = []
        onConnectionResetRef.current?.()
        reconcile()
      }

      socket.onmessage = (event: MessageEvent) => {
        let msg
        try { msg = JSON.parse(event.data as string) } catch { return }

        if (msg.type === 'subscribed') {
          attempt = 0
          // Set here rather than when prices arrive: a reconnect where nothing
          // traded brings a snapshot that `apply` drops as not newer.
          if (!cancelled) setStatus('connected')
          pendingRef.current = pendingRef.current.filter(id => id !== msg.market_id)
          ackedRef.current.push(msg.market_id as string)
          reportAcked()
        } else if (msg.type === 'price') {
          apply(msg as PriceFrame)
        } else if (msg.type === 'order') {
          onOrderFrameRef.current?.(msg as OrderFrame)
        } else if (msg.type === 'error' && msg.error?.code === 'too_many_subscriptions') {
          // The frame names no market, so the oldest subscribe still waiting
          // for an answer is the one that was refused.
          const refused = pendingRef.current.shift()
          if (refused !== undefined) {
            subscribedRef.current.delete(refused)
            onRefusedRef.current?.([refused])
          }
          // A refusal is an answer too, and may be the last one outstanding.
          reportAcked()
        } else if (msg.type === 'error' && msg.error?.code === 'invalid_market_id') {
          // A market id the relay could not read answers its subscribe as
          // surely as an ack does, and the acks after it wait for every
          // subscribe to be answered. Nothing falls back to polling for it: an
          // id the relay cannot parse is one the ledger cannot price either.
          const rejected = pendingRef.current.shift()
          if (rejected !== undefined) subscribedRef.current.delete(rejected)
          reportAcked()
        }
      }

      socket.onclose = (event: CloseEvent) => {
        if (cancelled) return
        subscribedRef.current = new Set()
        pendingRef.current = []
        ackedRef.current = []

        // Origin refused: retrying cannot help. Mark degraded but keep the
        // last known prices visible (AC 7).
        if (event.code === 4403) {
          setStatus('degraded')
          return
        }

        // Keep the last known prices while reconnecting (AC 7: retain stale
        // prices during temporary loss rather than blanking the display).
        setStatus('reconnecting')

        const delay = Math.min(1000 * 2 ** attempt, MAX_RETRY_MS)
        attempt += 1
        const reconnect = () => {
          if (!cancelled) retryTimer = setTimeout(connect, delay)
        }

        // 4401 / 4408: the access token expired. A browser socket cannot send
        // a fresh one, so refresh the cookie first — any request through `api`
        // does that via its interceptor, and a snapshot is one we need anyway.
        // If even that is refused, the session is over; stop.
        if (event.code === 4401 || event.code === 4408) {
          const refresh = onSessionRefreshRef.current
          if (!refresh) {
            reconnect()
            return
          }
          // The refresh is about the markets followed when the socket dropped.
          // A caller that has moved on since — the detail page navigating to
          // another market — must not have this answer applied to what it
          // shows now: a 401 for a book nobody is looking at would mark the
          // one on screen degraded.
          const asked = wantedRef.current.join(',')
          const stillWanted = () => wantedRef.current.join(',') === asked
          refresh().then(snapshot => {
            if (stillWanted()) apply(snapshot)
          }).then(reconnect, (err: unknown) => {
            if (!stillWanted()) return
            if (axios.isAxiosError(err) && err.response?.status === 401) {
              setStatus('degraded')
              return
            }
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
      const socket = socketRef.current
      if (socket?.readyState === WebSocket.OPEN) {
        for (const id of subscribedRef.current) {
          socket.send(JSON.stringify({ action: 'unsubscribe', market_id: id }))
        }
      }
      socket?.close()
      socketRef.current = null
      // StrictMode mounts, unmounts and remounts, and the second mount starts
      // from empty state — so the record of what has been read has to start
      // over with it, or a market read only on the cancelled first mount is
      // never read again and its card waits for a price for good (#104
      // amendment D). The app runs in StrictMode, so this is every page load
      // in development.
      readRef.current = new Set()
    }
  }, [])

  // The followed set, reconciled against what the connection holds. Keyed on
  // the ids themselves and not the array, so a caller may build a fresh one
  // each render.
  const followed = marketIds.join(',')
  useEffect(() => {
    reconcileRef.current()

    // The caller's own read, once for each market newly followed, before and
    // beside the socket: a realtime service that is down or refuses the origin
    // must not keep the ledger's price off the page (AC 1, AC 7).
    //
    // Keyed on the market and not on mount, because a page whose set starts
    // empty — the detail page, whose market id is null until the market loads
    // — has nothing to ask about at mount, and because "Load more" should read
    // only the markets it appended.
    const fresh = marketIds.filter(id => !readRef.current.has(id))
    if (fresh.length > 0) {
      for (const id of fresh) readRef.current.add(id)
      onMountRef.current?.(applyRef.current, fresh)
    }
    // `followed` is the dependency; `marketIds` is read through it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followed])

  // One identity for the life of the hook: callers put it in effect
  // dependencies, and a new function each render would re-run them.
  const applySnapshot = useCallback((snapshot: PriceState) => applyRef.current(snapshot), [])

  return { snapshotsByMarket, status, applySnapshot }
}
