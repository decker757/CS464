import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { type PublicMarketDetail, getMarket } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import { type MarketStatus, tradingStopped } from '../components/markets/marketStatus'
import StatusBadge from '../components/markets/StatusBadge'
import TradingPanel from '../components/markets/TradingPanel'
import BackLink from '../components/ui/BackLink'
import Card from '../components/ui/Card'
import SectionCard from '../components/ui/SectionCard'
import { useMarketPrices } from '../hooks/useMarketPrices'
import { formatPrice } from '../utils/formatPrice'

// setTimeout overflows a 32-bit int at ~24.8 days; a close further out than
// that gets no timer, since the page will be refetched long before then.
const MAX_TIMEOUT_MS = 2 ** 31 - 1

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-SG', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// Why trading is closed, for a market that cannot be traded right now.
function closedMessage(status: MarketStatus): string {
  if (status === 'pending_resolution') return 'Trading is closed while the outcome is decided.'
  if (status === 'approved') return 'This market has been settled. Trading is closed.'
  return 'Trading is closed for this market.'
}

function Banner({ className, children }: { className: string; children: React.ReactNode }) {
  return <div className={`mb-6 rounded-xl border px-5 py-3.5 text-sm ${className}`}>{children}</div>
}

function DetailItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold tracking-[0.5px] text-subtle uppercase">{label}</p>
      {children}
    </div>
  )
}

export default function MarketDetailPage() {
  const { id } = useParams<{ id: string }>()
  // Tagged with the market id it was loaded for, so moving to another market
  // shows that one loading rather than the last one's market or error.
  const [loaded, setLoaded] = useState<{ id: string; market: PublicMarketDetail | null; failed: boolean } | null>(null)
  // The market whose close_time has passed while this page was open. An id
  // rather than a flag, so it cannot carry over to the next market.
  const [closedMarketId, setClosedMarketId] = useState<string | null>(null)

  const current = loaded?.id === id ? loaded : null
  const market = current?.market ?? null
  const loading = current === null
  const error = current?.failed ?? false
  const pastClose = market !== null && closedMarketId === market.id

  const { prices, status: priceStatus } = useMarketPrices(market?.id ?? null)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    getMarket(id).then(
      found => { if (!cancelled) setLoaded({ id, market: found, failed: false }) },
      () => { if (!cancelled) setLoaded({ id, market: null, failed: true }) },
    )
    return () => { cancelled = true }
  }, [id])

  // [X-3] #36: "a countdown hitting zero is the event that disables" trading,
  // so the controls lock the moment close_time passes, without a refetch.
  const marketId = market?.id
  const closeTime = market?.close_time
  useEffect(() => {
    if (!marketId || !closeTime) return
    const ms = new Date(closeTime).getTime() - Date.now()
    if (ms > MAX_TIMEOUT_MS) return
    // A close_time already past still goes through a 0 ms timer: calling
    // setState directly in the effect is what the set-state-in-effect lint
    // rule forbids, so do not "simplify" this back to an early setState.
    const timer = setTimeout(() => setClosedMarketId(marketId), Math.max(ms, 0))
    return () => clearTimeout(timer)
  }, [marketId, closeTime])

  const tradeable = market?.status === 'open' && !pastClose

  // An early close leaves close_time in the future, so "Closed <close_time>"
  // would name a date that has not happened yet.
  function closeLabel(m: PublicMarketDetail): string {
    if (!pastClose && !tradingStopped(m.status, m.close_time)) return `Closes ${formatDate(m.close_time)}`
    if (pastClose) return `Closed ${formatDate(m.close_time)}`
    return 'Closed early'
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
              <StatusBadge status={market.status} />
              <span className="text-[13px] text-subtle">{closeLabel(market)}</span>
            </div>
            <h1 className="text-[26px] leading-[1.4] font-extrabold tracking-[-0.3px] text-smu-navy">{market.question}</h1>
            {market.description && (
              <p className="mt-3 text-sm leading-[1.6] text-muted">{market.description}</p>
            )}
          </div>

          {winningOutcome && (
            <Banner className="border-info/20 bg-info/5 font-semibold text-info-strong">
              Settled: <strong>{winningOutcome.label}</strong>
            </Banner>
          )}

          {market.status === 'pending_resolution' && (
            <Banner className="border-warning/20 bg-warning/5 text-warning-strong">
              Awaiting outcome decision from a second administrator.
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
              <TradingPanel marketId={market.id} outcomes={market.outcomes} />
            ) : (
              <p className="text-sm text-muted">{closedMessage(market.status)}</p>
            )}
          </Card>

          <SectionCard title="About this market">
            <div className="flex flex-col gap-4 text-sm text-smu-navy">
              <DetailItem label="Resolution criteria">
                <p className="leading-[1.6]">{market.resolution_criteria}</p>
              </DetailItem>
              <div className="grid grid-cols-2 gap-4">
                <DetailItem label="Closes">
                  <p>{formatDate(market.close_time)}</p>
                </DetailItem>
                <DetailItem label="Resolves by">
                  <p>{formatDate(market.resolution_time)}</p>
                </DetailItem>
              </div>
              {market.resolution_sources.length > 0 && (
                <DetailItem label="Resolution sources">
                  <ul className="mt-1 flex flex-col gap-1">
                    {market.resolution_sources.map(src => (
                      <li key={src.id}>
                        <a href={src.url} target="_blank" rel="noopener noreferrer" className="text-info">
                          {src.label ?? src.url}
                        </a>
                      </li>
                    ))}
                  </ul>
                </DetailItem>
              )}
            </div>
          </SectionCard>
        </>
      )}
    </AppLayout>
  )
}
