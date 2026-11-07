import { useRef } from 'react'
import { isSameTrade, type SentTrade } from '../utils/sentTrade'

/** A trade's key, kept until the ledger answers it definitely.
 *  `mayHaveCommitted` once an attempt under it got no answer or a 5xx. */
interface KeyAwaitingAnswer {
  key: string
  trade: SentTrade
  mayHaveCommitted: boolean
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
 * refuse as idempotency_key_reused (#580). An unanswered trade's key lasts as
 * long as the panel: if it did go through, placing the same trade again on
 * purpose replays its 201 and buys nothing, and the confirmation says one
 * trade was made (`sentMoreThanOnce`). Not to be cut short: a shorter life is
 * how the retry stops being safe.
 */
export function useTradeKeys() {
  const keysAwaitingAnswerRef = useRef<KeyAwaitingAnswer[]>([])

  // The entry for the key a trade already went out under, if it is still
  // awaiting a definite answer, or a new one for it.
  const keyFor = (trade: SentTrade): KeyAwaitingAnswer => {
    const awaiting = keysAwaitingAnswerRef.current.find(entry => isSameTrade(entry.trade, trade))
    if (awaiting) return awaiting
    const entry = { key: crypto.randomUUID(), trade, mayHaveCommitted: false }
    keysAwaitingAnswerRef.current.push(entry)
    return entry
  }

  const forgetKey = (key: string) => {
    keysAwaitingAnswerRef.current = keysAwaitingAnswerRef.current.filter(entry => entry.key !== key)
  }

  return { keyFor, forgetKey }
}
