import { useCallback, useEffect, useState } from 'react'
import { getMyPortfolio, type Position } from '../api/ledgerApi'

export interface MyPositions {
  /**
   * Every outcome the trader holds, by market id. A market absent from the
   * map is one they hold nothing in — which is most of them, and the reason
   * a card asks rather than being told.
   *
   * The positions are the ledger's own rows, so a card reads `quantity` and
   * matches `outcome_id` against the outcomes it already has. Nothing here
   * needs market_service: the browse list carries the labels already, which
   * is why this does not go through `utils/marketLabels` the way the
   * portfolio table has to.
   */
  positionsByMarket: Map<string, Position[]>
  /** Reads the portfolio again — after a trade, or a card's retry. */
  refetch: () => void
}

// Every position the signed-in trader holds, for the browse list's cards
// ([X-1] #450).
//
// One request for the page, not one per card. `GET /ledger/portfolio/me`
// already returns every position a trader holds, so a list of fifty cards
// costs exactly one read however many of them the trader is in — the same
// distinction #432 made for the prices.
//
// A failure is silent on purpose. The holding is an enhancement on a card
// whose job is the price: a portfolio the ledger would not answer leaves the
// cards exactly as they were, rather than taking the list down with it
// (AC 5). Nothing retries on a timer either — a holding changes when the
// trader trades, and the pages that let them trade re-read it themselves.
export function useMyPositions(): MyPositions {
  const [positionsByMarket, setPositionsByMarket] = useState<Map<string, Position[]>>(new Map())
  // Bumped by `refetch`, and read by the effect below, which is what asks the
  // ledger again.
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    // Set by the cleanup, so an answer for a page the trader has left — or a
    // read a newer one has replaced — cannot write into it.
    let cancelled = false

    getMyPortfolio().then(
      portfolio => {
        if (cancelled) return
        const byMarket = new Map<string, Position[]>()
        for (const position of portfolio.positions) {
          const held = byMarket.get(position.market_id)
          if (held) {
            held.push(position)
          } else {
            byMarket.set(position.market_id, [position])
          }
        }
        setPositionsByMarket(byMarket)
      },
      () => {},
    )

    return () => { cancelled = true }
  }, [attempt])

  const refetch = useCallback(() => setAttempt(count => count + 1), [])

  return { positionsByMarket, refetch }
}
