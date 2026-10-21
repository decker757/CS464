import { useEffect, useRef, useState } from 'react'
import { lookupUsernames } from '../api/authApi'

// The most ids auth names in one request, which is also the most trades the
// ledger puts on one page (auth-service.md, ledger-service.md).
const MAX_IDS_PER_REQUEST = 200

/**
 * Usernames by user id, for the rows of a service that names people only by
 * id — the ledger's trade feed, and an administrator's read of one user's
 * history ([2.4] #434, through #273's lookup). An id still on its way, one
 * auth does not know, and one a failed request never named are all simply
 * absent from the map, so the caller shows its fallback (the short id) and
 * never a blank.
 *
 * One request for the whole page rather than one per row: the ids are asked
 * about in a single batch, and only the ones not asked about before, so
 * "Load more" names the rows it adds and leaves the rest alone.
 *
 * Give `userIds` a stable identity — `useMemo` over whatever list it is
 * derived from — since a fresh array every render would send the effect round
 * again.
 */
export function useUsernames(userIds: string[]): Map<string, string> {
  const [namesById, setNamesById] = useState<Map<string, string>>(new Map())
  // An id counts as asked about when its request settles, not when it is
  // sent: a request the cleanup cancelled has to be sent again, and
  // StrictMode cancels the first mount's on purpose. usePricesForMarkets'
  // answeredIds is the same idea for the same reason.
  const askedIds = useRef(new Set<string>())

  useEffect(() => {
    const unasked = [...new Set(userIds)].filter(id => !askedIds.current.has(id))
    if (unasked.length === 0) return
    // One page of trades is at most 200 rows, so this truncates nothing
    // today; were a longer list ever passed, the ids past the limit would
    // keep their fallback rather than make the request a 422.
    const asking = unasked.slice(0, MAX_IDS_PER_REQUEST)
    let cancelled = false

    lookupUsernames(asking).then(
      users => {
        if (cancelled) return
        for (const id of asking) askedIds.current.add(id)
        setNamesById(previous => {
          const named = new Map(previous)
          for (const user of users) named.set(user.id, user.username)
          return named
        })
      },
      // A failure is an answer too. The names sit on top of rows that are
      // already on screen, so a refused or unreachable lookup leaves every
      // row on its short id and is not asked about again — it must never
      // empty the table it was only labelling.
      () => {
        if (cancelled) return
        for (const id of asking) askedIds.current.add(id)
      },
    )

    return () => { cancelled = true }
  }, [userIds])

  return namesById
}
