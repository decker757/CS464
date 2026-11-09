import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { errorCode } from '../api/errors'
import { getTradeByKey, type TradeResult } from '../api/ledgerApi'
import { useBalance } from '../context/BalanceContext'
import { isSameTrade, type SentTrade, type TradeCheck } from '../utils/sentTrade'
import { readStoredTrades, removeStoredTrade, storeTrade, sweepExpiredTrades, type StoredTrade } from '../utils/unconfirmedTrades'

/** A trade's key, kept until the ledger answers it definitely.
 *  `mayHaveCommitted` once an attempt under it got no answer or a 5xx.
 *  `isInFlight` while an attempt under it from this page has not ended.
 *  `sentAt` is when the latest attempt went out, or ended without an answer,
 *  in epoch milliseconds: what the wait below is counted from. */
interface KeyAwaitingAnswer {
  key: string
  trade: SentTrade
  mayHaveCommitted: boolean
  isInFlight: boolean
  sentAt: number
}

// How long after a trade's latest attempt was sent, or ended without an
// answer, a "not found" is believed. A trade still queued on the ledger has
// written nothing yet, so an earlier "not found" can be followed by its commit
// (ledger-service.md, the trade lookup). Nothing on the server bounds a
// trade's wait (#669 was closed), so this is a margin well past the 60 s
// gateway timeout and anything measured. Before then, the panel asks again.
const NOT_FOUND_IS_FINAL_AFTER_MS = 300_000

interface UseTradeKeysOptions {
  marketId: string
  traderId: string
  // A trade left unconfirmed before this load turned out to have committed,
  // so the holdings may be stale. The hook re-reads the balance itself.
  onFoundCommitted: () => void
}

/** The trade the ledger found under a key, in the terms the panel compares
 *  trades in. */
function sentTradeOf(found: TradeResult, outcomeLabel: string): SentTrade {
  return { outcomeId: found.outcome_id, outcomeLabel, side: found.side, quantity: found.quantity }
}

// Held, in whichever tab sends it, for as long as an attempt under a key is
// out (Web Locks, which every tab of the site shares). Absent in an old
// browser, or in part, where only this page's own attempts are known to be
// out.
function attemptLockOf(key: string): string {
  return `trade-attempt:${key}`
}

function hasWebLocks(): boolean {
  if (typeof navigator === 'undefined' || !('locks' in navigator)) return false
  const { locks } = navigator as { locks?: Partial<LockManager> }
  return typeof locks?.request === 'function' && typeof locks.query === 'function'
}

/** Whether an attempt under `key` is out in any tab, this one included. */
async function isAttemptOutAnywhere(key: string): Promise<boolean> {
  if (!hasWebLocks()) return false
  const { held = [], pending = [] } = await navigator.locks.query()
  return [...held, ...pending].some(lock => lock.name === attemptLockOf(key))
}

/**
 * Every trade sent and not yet answered definitely, each with its key. A key
 * is dropped only once the server has given a definite answer: success, or a
 * 4xx that means the trade did not happen. A lost reply or a 5xx means the
 * server may have committed it anyway, and the only safe retry is one that
 * reuses the key so the server's own idempotency check recognises it as the
 * same trade rather than a new one (ledger-service.md's idempotency section) —
 * throwing the key away on every failure, including these, is how a dropped
 * connection buys twice.
 *
 * Kept per trade, so the retry is safe whatever was traded in between, and a
 * different trade never goes out under another's key, which the ledger would
 * refuse as idempotency_key_reused (#580).
 *
 * A key is also kept in the browser's storage while its trade is unanswered,
 * so it outlives a reload or a visit elsewhere, and every tab reuses it
 * (#585). When a market page loads with one stored, it asks the ledger, says
 * what it found, and forgets the key once the answer is definite. Until then
 * a retry in any tab still reuses the key.
 */
export function useTradeKeys({ marketId, traderId, onFoundCommitted }: UseTradeKeysOptions) {
  const { refetch: refetchBalance } = useBalance()
  const keysAwaitingAnswerRef = useRef<KeyAwaitingAnswer[]>([])
  const [checks, setChecks] = useState<TradeCheck[]>([])

  const awaitingEntryOf = (key: string) => keysAwaitingAnswerRef.current.find(entry => entry.key === key)

  const dropEntry = (key: string) => {
    keysAwaitingAnswerRef.current = keysAwaitingAnswerRef.current.filter(entry => entry.key !== key)
  }

  const setCheck = (check: TradeCheck) => {
    setChecks(current => [...current.filter(other => other.key !== check.key), check])
  }

  const store = (entry: KeyAwaitingAnswer) => {
    storeTrade(traderId, marketId, { key: entry.key, trade: entry.trade, sentAt: entry.sentAt })
  }

  /** This page's entry for a key read from storage, made if it has none: a
   *  key another tab sent may have committed, as far as this page knows. */
  const adopt = (stored: StoredTrade): KeyAwaitingAnswer => {
    const known = awaitingEntryOf(stored.key)
    if (known) return known
    const entry = { key: stored.key, trade: stored.trade, mayHaveCommitted: true, isInFlight: false, sentAt: stored.sentAt }
    keysAwaitingAnswerRef.current.push(entry)
    return entry
  }

  // The key a trade already went out under, in this tab or another, if it is
  // still awaiting a definite answer, or a new one for it. A key this page
  // still holds is reused even after another tab got its answer: this page
  // may have told the trader that sending it again is safe, and the ledger
  // replays it, so the repeat says one trade was made. The click after that
  // is a new trade.
  const keyFor = (trade: SentTrade): KeyAwaitingAnswer => {
    const shared = readStoredTrades(traderId, marketId)?.find(other => isSameTrade(other.trade, trade))
    if (shared) return adopt(shared)
    const awaiting = keysAwaitingAnswerRef.current.find(entry => isSameTrade(entry.trade, trade))
    if (awaiting) return awaiting
    const entry = { key: crypto.randomUUID(), trade, mayHaveCommitted: false, isInFlight: false, sentAt: Date.now() }
    keysAwaitingAnswerRef.current.push(entry)
    return entry
  }

  const forgetKey = (key: string) => {
    dropEntry(key)
    removeStoredTrade(traderId, marketId, key)
    setChecks(current => current.filter(check => check.key !== key))
  }

  /** Sends an attempt under `entry`'s key. Kept beyond this page from the
   *  moment it is sent, not from when it fails: a reload while it is in
   *  flight gets no answer either, and it may still commit. The attempt's
   *  lock tells every tab it is out until it ends. */
  const sendUnder = async <T>(entry: KeyAwaitingAnswer, send: () => Promise<T>): Promise<T> => {
    entry.isInFlight = true
    entry.sentAt = Date.now()
    store(entry)
    if (!hasWebLocks()) return send()
    return navigator.locks.request(attemptLockOf(entry.key), send)
  }

  /** An attempt under `entry`'s key got no answer or a 5xx, so it may still
   *  commit, and the wait starts again from now. Not stored again when a
   *  check found the trade committed while this attempt was out: that check
   *  has forgotten the key, and this attempt could only have replayed it. */
  const markMayHaveCommitted = (entry: KeyAwaitingAnswer) => {
    entry.mayHaveCommitted = true
    entry.isInFlight = false
    entry.sentAt = Date.now()
    if (!awaitingEntryOf(entry.key)) return
    store(entry)
  }

  /** The ledger refused this attempt and wrote nothing, but the panel keeps
   *  the key for the trader's next confirm of the same trade. Nothing is left
   *  for a reload to ask about until that confirm goes out, unless an earlier
   *  attempt under the key got no answer: that one may still commit. */
  const markRefused = (entry: KeyAwaitingAnswer) => {
    entry.isInFlight = false
    if (entry.mayHaveCommitted) return
    removeStoredTrade(traderId, marketId, entry.key)
  }

  /** Clears what the checks said once the trader sends another trade, as the
   *  panel's other notices are. A check still waiting on the ledger stays. */
  const dismissAnsweredChecks = () => {
    setChecks(current => current.filter(check => check.answer === 'checking'))
  }

  const reportFoundCommitted = useEffectEvent(() => {
    void refetchBalance()
    onFoundCommitted()
  })

  useEffect(() => {
    let isCancelled = false
    const retryTimers: ReturnType<typeof setTimeout>[] = []

    const settle = (key: string, check: TradeCheck) => {
      setCheck(check)
      if (check.answer === 'checking' || check.answer === 'unknown') return
      dropEntry(key)
      removeStoredTrade(traderId, marketId, key)
    }

    /** When the wait runs out for `entry`, or null while an attempt under
     *  it is out in any tab. Counted from its latest attempt in any tab: one
     *  sent elsewhere while this was being asked may itself be queued. */
    const finalAfter = async (entry: KeyAwaitingAnswer): Promise<number | null> => {
      if (entry.isInFlight || await isAttemptOutAnywhere(entry.key)) return null
      const sharedSentAt = readStoredTrades(traderId, marketId)?.find(stored => stored.key === entry.key)?.sentAt ?? 0
      return Math.max(entry.sentAt, sharedSentAt) + NOT_FOUND_IS_FINAL_AFTER_MS
    }

    const ask = async (key: string, trade: SentTrade) => {
      let found: TradeResult | null = null
      let canBeAsked = true
      try {
        found = await getTradeByKey(marketId, key)
      } catch (err) {
        canBeAsked = errorCode(err) === 'trade_not_found'
      }
      // A retry answered meanwhile has forgotten the key, and said so itself.
      const entry = awaitingEntryOf(key)
      if (isCancelled || !entry) return

      if (!canBeAsked) {
        settle(key, { key, trade, answer: 'unknown' })
      } else if (found && isSameTrade(sentTradeOf(found, trade.outcomeLabel), trade)) {
        settle(key, { key, trade, answer: 'went-through', total: found.total })
        reportFoundCommitted()
      } else if (found) {
        // Another trade under this key, so this one was refused
        // idempotency_key_reused (ledger-service.md, the trade lookup).
        settle(key, { key, trade, answer: 'did-not-go-through' })
      } else {
        const finalAt = await finalAfter(entry)
        if (isCancelled || !awaitingEntryOf(key)) return
        const waitLeft = finalAt === null ? NOT_FOUND_IS_FINAL_AFTER_MS : finalAt - Date.now()
        if (waitLeft <= 0) {
          settle(key, { key, trade, answer: 'did-not-go-through' })
        } else {
          settle(key, { key, trade, answer: 'checking' })
          retryTimers.push(setTimeout(() => void ask(key, trade), waitLeft))
        }
      }
    }

    // What the checks said is about the market and trader they were made for.
    setChecks([])
    sweepExpiredTrades()
    for (const stored of readStoredTrades(traderId, marketId) ?? []) {
      adopt(stored)
      setCheck({ key: stored.key, trade: stored.trade, answer: 'checking' })
      void ask(stored.key, stored.trade)
    }

    return () => {
      isCancelled = true
      retryTimers.forEach(clearTimeout)
    }
  }, [marketId, traderId])

  return { keyFor, forgetKey, sendUnder, markMayHaveCommitted, markRefused, checks, dismissAnsweredChecks }
}
