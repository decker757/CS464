import type { SentTrade } from './sentTrade'

/** A trade sent and not yet answered definitely, kept in the tab's storage
 *  so its key outlives a reload (#585). `sentAt` is when its latest attempt
 *  went out, or ended without an answer, in milliseconds since the epoch. */
export interface StoredTrade {
  key: string
  trade: SentTrade
  sentAt: number
}

// Per trader and market, so a trader who logs in after another on this tab
// is never asked about, or retries under, the other's trades.
function storageKeyOf(traderId: string, marketId: string): string {
  return `unconfirmed-trades:${traderId}:${marketId}`
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
function writeStoredTrades(traderId: string, marketId: string, trades: StoredTrade[]): void {
  try {
    if (trades.length === 0) sessionStorage.removeItem(storageKeyOf(traderId, marketId))
    else sessionStorage.setItem(storageKeyOf(traderId, marketId), JSON.stringify(trades))
  } catch {
    // Nothing to do: see above.
  }
}

/** The trades this trader left unconfirmed on this market in this tab, or
 *  none when storage is unreadable or holds something else. */
export function readStoredTrades(traderId: string, marketId: string): StoredTrade[] {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(storageKeyOf(traderId, marketId)) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(isStoredTrade) : []
  } catch {
    return []
  }
}

/** Keeps `stored`, replacing any entry under the same key. */
export function storeTrade(traderId: string, marketId: string, stored: StoredTrade): void {
  const others = readStoredTrades(traderId, marketId).filter(trade => trade.key !== stored.key)
  writeStoredTrades(traderId, marketId, [...others, stored])
}

export function removeStoredTrade(traderId: string, marketId: string, key: string): void {
  writeStoredTrades(traderId, marketId, readStoredTrades(traderId, marketId).filter(trade => trade.key !== key))
}
