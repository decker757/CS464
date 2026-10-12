/** The first 8 characters of a UUID, for a column that stands in for a name
 * the backend does not have yet (MarketTradeTable's `user_id`, until an
 * admin batch lookup on auth exists). Not a hash or a slug — just truncated,
 * so two different ids are still visibly different ids. */
export function shortId(id: string): string {
  return id.slice(0, 8)
}
