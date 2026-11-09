import type { SentTrade } from './sentTrade'

/** A trade sent and not yet answered definitely, kept in the browser's
 *  storage so its key outlives a reload and is shared by every tab (#585).
 *  `sentAt` is when its latest attempt went out, or ended without an answer,
 *  in milliseconds since the epoch. */
export interface StoredTrade {
  key: string
  trade: SentTrade
  sentAt: number
}

// How long a stored trade is kept if no market page asks about it. A trade
// sent a day ago has long since committed or not, so all a dropped entry
// loses is the message saying which; the portfolio still shows it.
const KEPT_FOR_MS = 24 * 60 * 60 * 1000

const STORAGE_PREFIX = 'unconfirmed-trades:'

// Per trader and market, so a trader who logs in after another in this
// browser is never asked about, or retries under, the other's trades.
function storageKeyOf(traderId: string, marketId: string): string {
  return `${STORAGE_PREFIX}${traderId}:${marketId}`
}

function isStoredTrade(value: unknown): value is StoredTrade {
  if (typeof value !== 'object' || value === null) return false
  const { key, trade, sentAt } = value as Partial<StoredTrade>
  return typeof key === 'string' && typeof sentAt === 'number' && typeof trade === 'object' && trade !== null
    && typeof trade.outcomeId === 'string' && typeof trade.outcomeLabel === 'string'
    && (trade.side === 'buy' || trade.side === 'sell') && typeof trade.quantity === 'string'
}

// Storage can throw, in a private window or with site data blocked. Then the
// key lives only as long as the panel, as it did before #585.
function writeStoredTrades(storageKey: string, trades: StoredTrade[]): void {
  try {
    if (trades.length === 0) localStorage.removeItem(storageKey)
    else localStorage.setItem(storageKey, JSON.stringify(trades))
  } catch {
    // Nothing to do: see above.
  }
}

// The entries under one storage key from the last day, deleting any older
// ones as well as skipping them. Null when storage cannot be read.
function readKeptTrades(storageKey: string): StoredTrade[] | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(localStorage.getItem(storageKey) ?? '[]')
  } catch {
    return null
  }
  if (!Array.isArray(parsed)) return []
  const valid = parsed.filter(isStoredTrade)
  const keptSince = Date.now() - KEPT_FOR_MS
  const kept = valid.filter(stored => stored.sentAt >= keptSince)
  if (kept.length < valid.length) writeStoredTrades(storageKey, kept)
  return kept
}

/** The trades this trader left unanswered on this market in this browser,
 *  in any tab, from the last day. Null when storage cannot be read. */
export function readStoredTrades(traderId: string, marketId: string): StoredTrade[] | null {
  return readKeptTrades(storageKeyOf(traderId, marketId))
}

/** Deletes every entry past its day, for every trader and market, so a
 *  trade's description does not stay on a shared computer for good when
 *  nobody opens that market there again. */
export function sweepExpiredTrades(): void {
  const storageKeys: string[] = []
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const storageKey = localStorage.key(index)
      if (storageKey?.startsWith(STORAGE_PREFIX)) storageKeys.push(storageKey)
    }
  } catch {
    return
  }
  for (const storageKey of storageKeys) readKeptTrades(storageKey)
}

/** Keeps `stored`, replacing any entry under the same key. */
export function storeTrade(traderId: string, marketId: string, stored: StoredTrade): void {
  const others = (readStoredTrades(traderId, marketId) ?? []).filter(trade => trade.key !== stored.key)
  writeStoredTrades(storageKeyOf(traderId, marketId), [...others, stored])
}

export function removeStoredTrade(traderId: string, marketId: string, key: string): void {
  const kept = (readStoredTrades(traderId, marketId) ?? []).filter(trade => trade.key !== key)
  writeStoredTrades(storageKeyOf(traderId, marketId), kept)
}
