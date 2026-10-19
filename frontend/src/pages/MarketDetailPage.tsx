import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getMyPortfolio } from '../api/ledgerApi'
import { type PublicMarketDetail, getMarket } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import { type MarketStatus, tradingStopped } from '../components/markets/marketStatus'
import ResolutionDetails from '../components/markets/ResolutionDetails'
import StatusBadge from '../components/markets/StatusBadge'
import TradingPanel from '../components/markets/TradingPanel'
import BackLink from '../components/ui/BackLink'
import Card from '../components/ui/Card'
import SectionCard from '../components/ui/SectionCard'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useMarketPrices } from '../hooks/useMarketPrices'
import { useWindowOpen } from '../hooks/useWindowOpen'
import { disputeWindowLine } from '../utils/formatCountdown'
import { formatDateTime as formatDate } from '../utils/formatDateTime'
import { formatPrice } from '../utils/formatPrice'

// Why trading is closed, for a market that cannot be traded right now.
function closedMessage(status: MarketStatus): string {
  if (status === 'pending_resolution') return 'Trading is closed while the outcome is decided.'
  if (status === 'approved') return 'The outcome is approved and payouts are pending. Trading is closed.'
  if (status === 'settled') return 'This market has been settled. Trading is closed.'
  return 'Trading is closed for this market.'
}

// What the banner calls the outcome on record: only a settled market is final,
// an approved one is waiting on the ledger's payout, and a pending one on a
// second administrator.
function outcomeBannerLabel(status: MarketStatus): string {
  if (status === 'settled') return 'Settled'
  if (status === 'approved') return 'Approved outcome'
  return 'Proposed outcome'
}

function Banner({ className, children }: { className: string; children: React.ReactNode }) {
  return <div className={`mb-6 rounded-xl border px-5 py-3.5 text-sm ${className}`}>{children}</div>
}

export default function MarketDetailPage() {
  const { id } = useParams<{ id: string }>()
  // Tagged with the market id it was loaded for, so moving to another market
  // shows that one loading rather than the last one's market or error.
  const [loaded, setLoaded] = useState<{ id: string; market: PublicMarketDetail | null; failed: boolean } | null>(null)

  const current = loaded?.id === id ? loaded : null
  const market = current?.market ?? null
  const loading = current === null
  const error = current?.failed ?? false
  // This page's heading is the question rather than a PageTitle, so it
  // names the tab itself. Null until the fetch lands, which leaves the
  // previous title up rather than blanking it for the round trip (#356).
  useDocumentTitle(market?.question)

  const { prices, status: priceStatus } = useMarketPrices(market?.id ?? null)
  // [X-3] #36: "a countdown hitting zero is the event that disables" trading,
  // so the controls lock the moment close_time passes, without a refetch.
  const closeTimeStillAhead = useWindowOpen(market?.close_time ?? null)
  const pastClose = market !== null && !closeTimeStillAhead
  // Re-evaluates the instant the dispute window closes, rather than only at
  // whatever render happens to run next — an approved market has no trades
  // to force one, so without this "Disputes close in 3h" could sit on
  // screen long after payout ([3.3] #11).
  const disputeWindowStillOpen = useWindowOpen(market?.dispute_window_ends_at ?? null)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    getMarket(id).then(
      found => { if (!cancelled) setLoaded({ id, market: found, failed: false }) },
      () => { if (!cancelled) setLoaded({ id, market: null, failed: true }) },
    )
    return () => { cancelled = true }
  }, [id])

  const tradeable = market?.status === 'open' && !pastClose

  // What the trader holds of each of this market's outcomes, for the sell
  // clamp ([T-2] #357). The portfolio route carries every market's positions
  // and names no questions or labels (ledger-service.md), so picking out this
  // market's is the page's own join.
  //
  // Tagged with the market it was read for, like `loaded` above: outcome ids
  // differ between markets, so an untagged map would read as "holds nothing"
  // on the next market until its own read landed, and clamp every sell on
  // that. Null says the holdings are unknown, which clamps nothing at all.
  const [held, setHeld] = useState<{ id: string; byOutcome: Record<string, string> } | null>(null)
  const heldByOutcome = held !== null && held.id === id ? held.byOutcome : null

  // The most recent read's own number, so an answer that arrives after a
  // newer one is dropped instead of overwriting it. BalanceContext keeps the
  // same counter and names this exact case — the refetch after a trade
  // answering before the mount's read does. The portfolio route values every
  // position and mints the signup grant on a first read, so it is the slower
  // of the two; without this, the pre-trade picture lands last and the panel
  // spends the rest of the visit refusing to sell a position the ledger holds,
  // with nothing to re-fire the read.
  const latestHeldReadRef = useRef(0)

  const refetchHeld = useCallback(() => {
    if (!id) return
    const requestId = ++latestHeldReadRef.current
    getMyPortfolio().then(
      portfolio => {
        if (requestId !== latestHeldReadRef.current) return
        const mine = portfolio.positions.filter(position => position.market_id === id)
        setHeld({ id, byOutcome: Object.fromEntries(mine.map(position => [position.outcome_id, position.quantity])) })
      },
      // Leave the last known holdings up rather than blanking them, the same
      // way BalanceContext keeps the last balance on a failed refetch.
      () => {},
    )
  }, [id])

  // Only for a market that can be traded: nothing else on this page uses the
  // figure, and a closed or settled market has no sell to clamp.
  useEffect(() => {
    if (tradeable) refetchHeld()
  }, [tradeable, refetchHeld])

  // An early close leaves close_time in the future, so "Closed <close_time>"
  // would name a date that has not happened yet.
  function closeLabel(m: PublicMarketDetail): string {
    if (!pastClose && !tradingStopped(m.status, m.close_time)) return `Closes ${formatDate(m.close_time)}`
    if (pastClose) return `Closed ${formatDate(m.close_time)}`
    return 'Closed early'
  }

  // The badge reads the same answer the panel is gated on rather than the raw
  // status column. The clock closes a market and the sweep writes CLOSED a
  // few seconds later (ADR 0011), so for that window the column still says
  // `open` on a market nobody can trade — and a green "Open" chip would sit
  // above the words "Trading is closed" (#357).
  function badgeStatus(m: PublicMarketDetail): string {
    return m.status === 'open' && !tradeable ? 'closed' : m.status
  }

  const winningOutcome = market?.proposed_outcome_id
    ? market.outcomes.find(o => o.id === market.proposed_outcome_id)
    : null

  return (
    <AppLayout width="max-w-[900px]">
      <BackLink to="/markets" label="Markets" className="mb-6 inline-block" />

      {loading && <p className="text-sm text-muted">Loading…</p>}

      {error && (
        <p role="alert" className="text-sm text-danger">
          Failed to load market. It may not exist or may not be published yet.
        </p>
      )}

      {market && (
        <>
          <div className="mb-8">
            <div className="mb-4 flex items-center gap-3">
              <StatusBadge status={badgeStatus(market)} />
              <span className="text-[13px] text-subtle">{closeLabel(market)}</span>
            </div>
            <h1 className="text-[26px] leading-[1.4] font-extrabold tracking-[-0.3px] text-smu-navy">{market.question}</h1>
            {market.description && (
              <p className="mt-3 text-sm leading-[1.6] text-muted">{market.description}</p>
            )}
          </div>

          {winningOutcome && (
            <Banner className="border-info/20 bg-info/5 font-semibold text-info-strong">
              {outcomeBannerLabel(market.status)}: <strong>{winningOutcome.label}</strong>
            </Banner>
          )}

          {market.status === 'pending_resolution' && (
            <Banner className="border-warning/20 bg-warning/5 text-warning-strong">
              Awaiting outcome decision from a second administrator.
            </Banner>
          )}

          {market.status === 'approved' && (
            <Banner className="border-warning/20 bg-warning/5 text-warning-strong">
              {disputeWindowLine(market.dispute_window_ends_at, disputeWindowStillOpen)}
            </Banner>
          )}

          <div className="mb-3 grid gap-3" style={{ gridTemplateColumns: `repeat(${market.outcomes.length}, 1fr)` }}>
            {market.outcomes.map(outcome => {
              const outcomePrice = prices?.find(p => p.outcome_id === outcome.id)
              const isWinner = outcome.id === market.proposed_outcome_id
              return (
                <Card key={outcome.id} borderColor={isWinner ? 'border-info/40' : undefined} className="px-6 py-5 text-center">
                  <p className="mb-2 text-[13px] font-semibold tracking-[0.5px] text-muted uppercase">{outcome.label}</p>
                  <p aria-label={`${outcome.label} price`} className="mb-1 text-[32px] font-extrabold tracking-[-1px] text-smu-navy">
                    {outcomePrice ? formatPrice(outcomePrice.price) : '—'}
                  </p>
                  <p className="text-xs text-subtle">{outcomePrice ? 'implied probability' : 'loading price…'}</p>
                </Card>
              )
            })}
          </div>

          {(priceStatus === 'reconnecting' || priceStatus === 'degraded') && (
            <div
              role="status"
              className={`mb-6 flex items-center gap-2 text-xs font-medium ${priceStatus === 'degraded' ? 'text-danger' : 'text-warning-strong'}`}
            >
              <span className={`h-2 w-2 shrink-0 rounded-full ${priceStatus === 'degraded' ? 'bg-danger' : 'animate-pulse bg-warning'}`} />
              {priceStatus === 'reconnecting' ? 'Reconnecting to live prices…' : 'Live prices unavailable.'}
            </div>
          )}

          <Card className="mb-6 px-6 py-5">
            {tradeable ? (
              <TradingPanel marketId={market.id} outcomes={market.outcomes} heldByOutcome={heldByOutcome} onTraded={refetchHeld} />
            ) : (
              <p className="text-sm text-muted">{closedMessage(market.status)}</p>
            )}
          </Card>

          <SectionCard title="About this market">
            <ResolutionDetails market={market} />
          </SectionCard>
        </>
      )}
    </AppLayout>
  )
}
