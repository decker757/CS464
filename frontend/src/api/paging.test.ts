import { describe, expect, it } from 'vitest'
import { fetchAllPages } from './paging'

describe('fetchAllPages', () => {
  it('walks every page, handing each next cursor to the following fetch', async () => {
    const pages: Record<string, { items: string[]; nextCursor: string | null }> = {
      first: { items: ['a', 'b'], nextCursor: 'second' },
      second: { items: ['c'], nextCursor: null },
    }
    const items = await fetchAllPages(async cursor => pages[cursor ?? 'first'])
    expect(items).toEqual(['a', 'b', 'c'])
  })

  it('stops with an error when the server returns the cursor it was just given', async () => {
    await expect(
      fetchAllPages(async cursor => ({ items: ['a'], nextCursor: cursor ?? 'stuck' })),
    ).rejects.toThrow(/same page cursor/)
  })

  it('ends the walk after a page that carries no next cursor at all', async () => {
    let fetches = 0
    const items = await fetchAllPages(async () => {
      fetches += 1
      return { items: ['a'] } as unknown as { items: string[]; nextCursor: string | null }
    })
    expect(items).toEqual(['a'])
    expect(fetches).toBe(1)
  })
})
