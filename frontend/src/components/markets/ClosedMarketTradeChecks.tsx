import { useTradeKeys } from '../../hooks/useTradeKeys'
import TradeCheckNotices from './TradeCheckNotices'

interface ClosedMarketTradeChecksProps {
  marketId: string
  traderId: string
  onFoundCommitted: () => void
}

/** On a market that no longer trades, there is no panel, but a trade sent
 *  just before it closed may still be unconfirmed: this asks the ledger about
 *  it and says what it found, as the panel does on an open market (#585). */
export default function ClosedMarketTradeChecks({ marketId, traderId, onFoundCommitted }: ClosedMarketTradeChecksProps) {
  const { checks } = useTradeKeys({ marketId, traderId, onFoundCommitted })
  return <TradeCheckNotices checks={checks} canTradeHere={false} />
}
