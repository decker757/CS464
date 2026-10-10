import type { LedgerEntry } from '../api/ledgerApi'
import type { PublicMarketDetail } from '../api/marketApi'
import { labelsFor, loadMarketsById } from './marketLabels'

export interface EntryRow extends LedgerEntry {
  // Only a trade row names a market and an outcome (ledger-service.md); the
  // grant, and any kind this app does not recognise, do not.
  isTrade: boolean
  question: string
  outcomeLabel: string
}

export interface EntryPage {
  entries: EntryRow[]
  next_cursor: string | null
}

function toRow(entry: LedgerEntry, markets: Map<string, PublicMarketDetail>): EntryRow {
  const isTrade = entry.kind === 'trade_buy' || entry.kind === 'trade_sell'
  if (!isTrade || !entry.market_id || !entry.outcome_id) {
    return { ...entry, isTrade: false, question: '', outcomeLabel: '' }
  }
  return { ...entry, isTrade: true, ...labelsFor(entry.market_id, entry.outcome_id, markets) }
}

/**
 * Joins one page of ledger entries against the markets their trades name,
 * for `usePagedList`'s `fetchPage` — it only knows how to walk pages, not
 * how to build a row. `fetchOnePage` does the actual `GET`, this does the
 * join every ledger-history screen (a trader's own, an admin's) needs.
 */
export async function joinEntryPage(
  fetchOnePage: () => Promise<{ entries: LedgerEntry[]; next_cursor: string | null }>,
): Promise<EntryPage> {
  const page = await fetchOnePage()
  const marketIds = page.entries.flatMap(entry => entry.market_id ? [entry.market_id] : [])
  const markets = await loadMarketsById(marketIds)
  return { entries: page.entries.map(entry => toRow(entry, markets)), next_cursor: page.next_cursor }
}

export const pagedEntryOptions = {
  itemsOf: (page: EntryPage) => page.entries,
  nextCursorOf: (page: EntryPage) => page.next_cursor,
  idOf: (row: EntryRow) => row.id,
}

// What moved: a human label for a recognised kind, and the kind itself,
// verbatim, for anything this app does not recognise — ledger-service.md
// says to treat an unrecognised kind as opaque, not an error.
export function kindLabel(kind: string): string {
  if (kind === 'signup_grant') return 'Starting grant'
  if (kind === 'trade_buy') return 'Buy'
  if (kind === 'trade_sell') return 'Sell'
  return kind
}
