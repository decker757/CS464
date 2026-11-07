import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { errorCode } from '../api/errors'
import { getTradeByKey, type TradeResult } from '../api/ledgerApi'
import { isSameTrade, type SentTrade } from '../utils/sentTrade'
import { readStoredTrades, removeStoredTrade, storeTrade, type StoredTrade } from '../utils/unconfirmedTrades'

/** A trade's key, kept until the ledger answers it definitely.
 *  `mayHaveCommitted` once an attempt under it got no answer or a 5xx.
 *  `isInFlight` while an attempt under it from this page has not ended.
 *  `sentAt` is when the latest attempt went out, or ended without an
 *  answer, in epoch milliseconds: what the minute below is counted from. */
interface KeyAwaitingAnswer {
  key: string
  trade: SentTrade
  mayHaveCommitted: boolean
  isInFlight: boolean
  sentAt: number
}

/** What the ledger said about a trade left unconfirmed before this page
 *  loaded (#585). `unknown` when it could not be asked: the key is kept. */
export type TradeCheck =
  | { key: string; trade: SentTrade; answer: 'checking' | 'did-not-go-through' | 'unknown' }
  | { key: string; trade: SentTrade; answer: 'went-through'; total: string }

// How long after a trade's latest attempt was sent, or ended without an
// answer, a "not found" is believed. A trade still queued on the ledger has
// written nothing yet, and nothing on the servers bounds that wait, so an
// earlier "not found" can be followed by its commit (ledger-service.md, the
// trade lookup). Before then, the panel asks again.
const NOT_FOUND_IS_FINAL_AFTER_MS = 60_000

interface UseTradeKeysOptions {
  marketId: string
  traderId: string
  // A trade left unconfirmed before this load turned out to have committed,
  // so the balance and holdings may be stale.
  onFoundCommitted: () => void
}

/** The trade the ledger found under a key, in the terms the panel compares
 *  trades in. */
function sentTradeOf(found: TradeResult, outcomeLabel: string): SentTrade {
  return { outcomeId: found.outcome_id, outcomeLabel, side: found.side, quantity: found.quantity }
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
 * A key is also kept in the tab's storage while its trade is unanswered, so it
 * outlives a reload or a visit elsewhere (#585). When the panel loads with one
 * stored, it asks the ledger, says what it found, and forgets the key once
 * the answer is definite. Until then a retry still reuses the key.
 */
export function useTradeKeys({ marketId, traderId, onFoundCommitted }: UseTradeKeysOptions) {
  const keysAwaitingAnswerRef = useRef<KeyAwaitingAnswer[]>([])
  const [checks, setChecks] = useState<TradeCheck[]>([])

  const awaitingEntryOf = (key: string) => keysAwaitingAnswerRef.current.find(entry => entry.key === key)

  const setCheck = (check: TradeCheck) => {
    setChecks(current => [...current.filter(other => other.key !== check.key), check])
  }

  // The entry for the key a trade already went out under, if it is still
  // awaiting a definite answer, or a new one for it.
  const keyFor = (trade: SentTrade): KeyAwaitingAnswer => {
    const awaiting = keysAwaitingAnswerRef.current.find(entry => isSameTrade(entry.trade, trade))
    if (awaiting) return awaiting
    const entry = { key: crypto.randomUUID(), trade, mayHaveCommitted: false, isInFlight: false, sentAt: Date.now() }
    keysAwaitingAnswerRef.current.push(entry)
    return entry
  }

  const forgetKey = (key: string) => {
    keysAwaitingAnswerRef.current = keysAwaitingAnswerRef.current.filter(entry => entry.key !== key)
    removeStoredTrade(traderId, marketId, key)
    setChecks(current => current.filter(check => check.key !== key))
  }

  /** An attempt under `entry`'s key is going out now. Kept beyond this page
   *  from the moment it is sent, not from when it fails: a reload while it is
   *  in flight gets no answer either, and it may still commit. */
  const markSent = (entry: KeyAwaitingAnswer) => {
    entry.isInFlight = true
    entry.sentAt = Date.now()
    storeTrade(traderId, marketId, { key: entry.key, trade: entry.trade, sentAt: entry.sentAt })
  }

  /** An attempt under `entry`'s key got no answer or a 5xx, so it may still
   *  commit, and the minute starts again from now. Unless a check found it
   *  committed while this attempt was out: the key is then gone for good,
   *  and this attempt could only have replayed it. */
  const markMayHaveCommitted = (entry: KeyAwaitingAnswer) => {
    entry.mayHaveCommitted = true
    entry.isInFlight = false
    entry.sentAt = Date.now()
    if (!awaitingEntryOf(entry.key)) return
    storeTrade(traderId, marketId, { key: entry.key, trade: entry.trade, sentAt: entry.sentAt })
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

  const reportFoundCommitted = useEffectEvent(onFoundCommitted)

  useEffect(() => {
    let isCancelled = false
    const retryTimers: ReturnType<typeof setTimeout>[] = []

    const settle = (stored: StoredTrade, check: TradeCheck) => {
      setCheck(check)
      if (check.answer === 'checking' || check.answer === 'unknown') return
      keysAwaitingAnswerRef.current = keysAwaitingAnswerRef.current.filter(entry => entry.key !== stored.key)
      removeStoredTrade(traderId, marketId, stored.key)
    }

    const ask = async (stored: StoredTrade) => {
      const { key, trade } = stored
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
        settle(stored, { key, trade, answer: 'unknown' })
      } else if (found && isSameTrade(sentTradeOf(found, trade.outcomeLabel), trade)) {
        settle(stored, { key, trade, answer: 'went-through', total: found.total })
        reportFoundCommitted()
      } else if (found) {
        // Another trade under this key, so this one was refused
        // idempotency_key_reused (ledger-service.md, the trade lookup).
        settle(stored, { key, trade, answer: 'did-not-go-through' })
      } else {
        // Not while an attempt from this page is still out, and only a minute
        // after the latest one went out or ended: a retry sent while this was
        // being asked may itself be queued on the ledger.
        const waitLeft = entry.isInFlight
          ? NOT_FOUND_IS_FINAL_AFTER_MS
          : entry.sentAt + NOT_FOUND_IS_FINAL_AFTER_MS - Date.now()
        if (waitLeft <= 0) {
          settle(stored, { key, trade, answer: 'did-not-go-through' })
        } else {
          settle(stored, { key, trade, answer: 'checking' })
          retryTimers.push(setTimeout(() => void ask(stored), waitLeft))
        }
      }
    }

    // What the checks said is about the market and trader they were made for.
    setChecks([])
    for (const stored of readStoredTrades(traderId, marketId)) {
      if (!awaitingEntryOf(stored.key)) {
        keysAwaitingAnswerRef.current.push({ key: stored.key, trade: stored.trade, mayHaveCommitted: true, isInFlight: false, sentAt: stored.sentAt })
      }
      setCheck({ key: stored.key, trade: stored.trade, answer: 'checking' })
      void ask(stored)
    }

    return () => {
      isCancelled = true
      retryTimers.forEach(clearTimeout)
    }
  }, [marketId, traderId])

  return { keyFor, forgetKey, markSent, markMayHaveCommitted, markRefused, checks, dismissAnsweredChecks }
}
