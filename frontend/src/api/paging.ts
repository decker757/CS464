export interface Page<Item> {
  items: Item[]
  /** Pass back to fetch the next page; null on the last page. */
  nextCursor: string | null
}

/** Every item of a keyset-paged list, fetching one page after another until the last. */
export async function fetchAllPages<Item>(fetchPage: (cursor: string | undefined) => Promise<Page<Item>>): Promise<Item[]> {
  const items: Item[] = []
  let cursor: string | undefined
  do {
    const page = await fetchPage(cursor)
    items.push(...page.items)
    cursor = page.nextCursor ?? undefined
  } while (cursor)
  return items
}
