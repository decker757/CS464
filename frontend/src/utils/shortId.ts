/** The first 8 characters of a UUID, for a cell with no name to show:
 * MarketTradeTable's User column, for an id auth's batch lookup did not name
 * ([2.4] #434). Not a hash or a slug — just truncated, so two different ids
 * are still visibly different ids. */
export function shortId(id: string): string {
  return id.slice(0, 8)
}
