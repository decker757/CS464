import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { type PublicMarketDetail, getMarket } from '../api/marketApi'
import AppNavbar from '../components/AppNavbar'
import { useMarketPrices } from '../hooks/useMarketPrices'
import { CREAM, NAV } from '../theme/colors'

const STATUS_CONFIG: Record<
  PublicMarketDetail['status'],
  { label: string; color: string; bg: string; border: string }
> = {
  open:               { label: 'Open',    color: '#16a34a', bg: 'rgba(22,163,74,0.1)',   border: 'rgba(22,163,74,0.25)' },
  closed:             { label: 'Closed',  color: '#6b7280', bg: 'rgba(107,114,128,0.1)', border: 'rgba(107,114,128,0.25)' },
  pending_resolution: { label: 'Pending', color: '#d97706', bg: 'rgba(217,119,6,0.1)',   border: 'rgba(217,119,6,0.25)' },
  approved:           { label: 'Settled', color: '#2563eb', bg: 'rgba(37,99,235,0.1)',   border: 'rgba(37,99,235,0.25)' },
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-SG', {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function formatPrice(price: string): string {
  return `${(parseFloat(price) * 100).toFixed(1)}%`
}

export default function MarketDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [market, setMarket] = useState<PublicMarketDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [pastClose, setPastClose] = useState(false)

  const prices = useMarketPrices(market?.id ?? null)

  useEffect(() => {
    if (!id) return
    getMarket(id)
      .then(setMarket)
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [id])

  // Disable trading controls in real time when close_time passes, without
  // waiting for a refetch. Spec [X-3] #36: "a countdown hitting zero is the
  // event that disables them."
  // setTimeout overflows a 32-bit int at ~24.8 days; skip the timer for
  // far-future closes — the page will be refetched long before then anyway.
  const MAX_TIMEOUT_MS = 2 ** 31 - 1
  useEffect(() => {
    if (!market) return
    const ms = new Date(market.close_time).getTime() - Date.now()
    if (ms <= 0) { setPastClose(true); return }
    if (ms > MAX_TIMEOUT_MS) return
    const t = setTimeout(() => setPastClose(true), ms)
    return () => clearTimeout(t)
  }, [market?.close_time])

  const tradeable = market?.status === 'open' && !pastClose

  const winningOutcome = market?.proposed_outcome_id
    ? market.outcomes.find(o => o.id === market.proposed_outcome_id)
    : null

  return (
    <div style={{ minHeight: '100vh', backgroundColor: CREAM }}>
      <AppNavbar />
      <main style={{ maxWidth: 900, margin: '0 auto', padding: '48px 32px' }}>
        <Link
          to="/markets"
          style={{ fontSize: 13, color: '#6b7280', textDecoration: 'none', display: 'inline-block', marginBottom: 24 }}
        >
          ← Markets
        </Link>

        {loading && (
          <p style={{ color: '#6b7280', fontSize: 14 }}>Loading…</p>
        )}

        {error && (
          <p role="alert" style={{ color: '#dc2626', fontSize: 14 }}>
            Failed to load market. It may not exist or may not be published yet.
          </p>
        )}

        {market && (
          <>
            {/* Header */}
            <div style={{ marginBottom: 32 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: STATUS_CONFIG[market.status].color,
                    backgroundColor: STATUS_CONFIG[market.status].bg,
                    border: `1px solid ${STATUS_CONFIG[market.status].border}`,
                    padding: '3px 10px',
                    borderRadius: 20,
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                  }}
                >
                  {STATUS_CONFIG[market.status].label}
                </span>
                <span style={{ fontSize: 13, color: '#9ca3af' }}>
                  {!pastClose
                    ? `Closes ${formatDate(market.close_time)}`
                    : `Closed ${formatDate(market.close_time)}`}
                </span>
              </div>
              <h1 style={{ fontSize: 26, fontWeight: 800, color: NAV, margin: 0, lineHeight: 1.4, letterSpacing: '-0.3px' }}>
                {market.question}
              </h1>
              {market.description && (
                <p style={{ fontSize: 14, color: '#6b7280', marginTop: 12, lineHeight: 1.6 }}>
                  {market.description}
                </p>
              )}
            </div>

            {/* Settled banner */}
            {winningOutcome && (
              <div
                style={{
                  backgroundColor: 'rgba(37,99,235,0.06)',
                  border: '1px solid rgba(37,99,235,0.2)',
                  borderRadius: 12,
                  padding: '14px 20px',
                  marginBottom: 24,
                  fontSize: 14,
                  color: '#1d4ed8',
                  fontWeight: 600,
                }}
              >
                Settled: <strong>{winningOutcome.label}</strong>
              </div>
            )}

            {/* Pending banner */}
            {market.status === 'pending_resolution' && (
              <div
                style={{
                  backgroundColor: 'rgba(217,119,6,0.06)',
                  border: '1px solid rgba(217,119,6,0.2)',
                  borderRadius: 12,
                  padding: '14px 20px',
                  marginBottom: 24,
                  fontSize: 14,
                  color: '#92400e',
                }}
              >
                Awaiting outcome decision from a second administrator.
              </div>
            )}

            {/* Outcomes + prices */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: `repeat(${market.outcomes.length}, 1fr)`,
                gap: 12,
                marginBottom: 32,
              }}
            >
              {market.outcomes.map(outcome => {
                const outcomePrice = prices?.find(p => p.outcome_id === outcome.id)
                const isWinner = outcome.id === market.proposed_outcome_id
                return (
                  <div
                    key={outcome.id}
                    style={{
                      backgroundColor: '#fff',
                      border: `1px solid ${isWinner ? 'rgba(37,99,235,0.4)' : 'rgba(182,145,70,0.15)'}`,
                      borderRadius: 16,
                      padding: '20px 24px',
                      boxShadow: '0 2px 8px rgba(21,30,85,0.05)',
                      textAlign: 'center',
                    }}
                  >
                    <p style={{ fontSize: 13, fontWeight: 600, color: '#6b7280', margin: '0 0 8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      {outcome.label}
                    </p>
                    <p
                      aria-label={`${outcome.label} price`}
                      style={{ fontSize: 32, fontWeight: 800, color: NAV, margin: '0 0 4px', letterSpacing: '-1px' }}
                    >
                      {outcomePrice ? formatPrice(outcomePrice.price) : '—'}
                    </p>
                    <p style={{ fontSize: 12, color: '#9ca3af', margin: 0 }}>
                      {outcomePrice ? 'implied probability' : 'no trades yet'}
                    </p>
                  </div>
                )
              })}
            </div>

            {/* Trading state */}
            <div
              style={{
                backgroundColor: '#fff',
                border: '1px solid rgba(182,145,70,0.15)',
                borderRadius: 16,
                padding: '20px 24px',
                marginBottom: 24,
                boxShadow: '0 2px 8px rgba(21,30,85,0.05)',
              }}
            >
              {tradeable ? (
                <div>
                  <div style={{ display: 'flex', gap: 10 }}>
                    {market.outcomes.map(outcome => (
                      <button
                        key={outcome.id}
                        type="button"
                        disabled
                        style={{
                          flex: 1,
                          padding: '12px',
                          borderRadius: 10,
                          border: '1px solid rgba(21,30,85,0.2)',
                          backgroundColor: CREAM,
                          color: NAV,
                          fontSize: 14,
                          fontWeight: 600,
                          cursor: 'not-allowed',
                          opacity: 0.6,
                        }}
                      >
                        Buy {outcome.label}
                      </button>
                    ))}
                  </div>
                  <p style={{ fontSize: 12, color: '#9ca3af', margin: '10px 0 0' }}>
                    Trading coming soon.
                  </p>
                </div>
              ) : (
                <p style={{ fontSize: 14, color: '#6b7280', margin: 0 }}>
                  {(!tradeable && market.status === 'open' || market.status === 'closed') && 'Trading is closed for this market.'}
                  {market.status === 'pending_resolution' && 'Trading is closed while the outcome is decided.'}
                  {market.status === 'approved' && 'This market has been settled. Trading is closed.'}
                </p>
              )}
            </div>

            {/* About */}
            <div
              style={{
                backgroundColor: '#fff',
                border: '1px solid rgba(182,145,70,0.15)',
                borderRadius: 16,
                padding: '24px 28px',
                boxShadow: '0 2px 8px rgba(21,30,85,0.05)',
              }}
            >
              <h2 style={{ fontSize: 15, fontWeight: 700, color: NAV, margin: '0 0 20px', paddingBottom: 12, borderBottom: '1px solid rgba(182,145,70,0.12)' }}>
                About this market
              </h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div>
                  <p style={{ fontSize: 12, fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.5px', margin: '0 0 4px' }}>
                    Resolution criteria
                  </p>
                  <p style={{ fontSize: 14, color: NAV, margin: 0, lineHeight: 1.6 }}>
                    {market.resolution_criteria}
                  </p>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  <div>
                    <p style={{ fontSize: 12, fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.5px', margin: '0 0 4px' }}>
                      Closes
                    </p>
                    <p style={{ fontSize: 14, color: NAV, margin: 0 }}>{formatDate(market.close_time)}</p>
                  </div>
                  <div>
                    <p style={{ fontSize: 12, fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.5px', margin: '0 0 4px' }}>
                      Resolves by
                    </p>
                    <p style={{ fontSize: 14, color: NAV, margin: 0 }}>{formatDate(market.resolution_time)}</p>
                  </div>
                </div>
                {market.resolution_sources.length > 0 && (
                  <div>
                    <p style={{ fontSize: 12, fontWeight: 600, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.5px', margin: '0 0 8px' }}>
                      Resolution sources
                    </p>
                    <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {market.resolution_sources.map(src => (
                        <li key={src.id}>
                          <a
                            href={src.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ fontSize: 14, color: '#2563eb', textDecoration: 'none' }}
                          >
                            {src.label ?? src.url}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
