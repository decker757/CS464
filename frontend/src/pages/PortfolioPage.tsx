import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMyPortfolio, type Portfolio, type Position } from '../api/ledgerApi'
import { getMarket, type PublicMarketDetail } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import Card from '../components/ui/Card'
import DetailItem from '../components/ui/DetailItem'
import PageTitle from '../components/ui/PageTitle'
import { formatCreditsPrecise, isZeroCredits } from '../utils/formatCredits'

interface PositionRow extends Position {
  question: string
  outcomeLabel: string
}

// Markets, keyed by id, for every distinct market a position names — one
// fetch per market held, not per position (ledger-service.md: the portfolio
// carries ids only, and labels are the frontend's own join). allSettled, not
// all: one market failing to load (market_service down, or a 404) must not
// blank the whole portfolio — cash and net worth came from the ledger and
// are correct regardless. toRow's "Unknown market" fallback covers the gap.
async function loadMarkets(positions: Position[]): Promise<Map<string, PublicMarketDetail>> {
  const ids = [...new Set(positions.map(p => p.market_id))]
  const results = await Promise.allSettled(ids.map(getMarket))
  const markets: PublicMarketDetail[] = []
  for (const result of results) {
    if (result.status === 'fulfilled') markets.push(result.value)
  }
  return new Map(markets.map(m => [m.id, m]))
}

function toRow(position: Position, markets: Map<string, PublicMarketDetail>): PositionRow {
  const market = markets.get(position.market_id)
  const outcome = market?.outcomes.find(o => o.id === position.outcome_id)
  return {
    ...position,
    question: market?.question ?? 'Unknown market',
    outcomeLabel: outcome?.label ?? 'Unknown outcome',
  }
}

// A fresh position's unrealized P&L is zero, not a gain — ADR 0018/[T-4] #24
// say it is never a gain on a book nobody else has traded. Only colour an
// actual loss or an actual gain; zero stays neutral.
function PnlCell({ pnl }: { pnl: string }) {
  const color = isZeroCredits(pnl) ? 'text-muted' : pnl.startsWith('-') ? 'text-danger' : 'text-success'
  return (
    <span className={color}>
      {formatCreditsPrecise(pnl, { showSign: true })}
    </span>
  )
}

/** A right-aligned table cell for a plain number or price — every row has several of these. */
function NumberCell({ children }: { children: React.ReactNode }) {
  return <td className="px-5 py-3 text-right text-smu-navy">{children}</td>
}

function HeaderCell({ align = 'left', children }: { align?: 'left' | 'right'; children: React.ReactNode }) {
  return <th className={`px-5 py-3 ${align === 'right' ? 'text-right' : ''}`}>{children}</th>
}

// Cash, positions value and net worth are all shown at the same precision
// formatCredits truncates to a whole number, so the three cards did not add
// up on screen (997 + 2 could show as 1,000, and a position worth 0.9000
// read as "0 credits"). formatCreditsPrecise keeps all four decimal places.
function StatCard({ label, value, emphasized = false }: { label: string; value: string; emphasized?: boolean }) {
  return (
    <Card className="px-6 py-5" borderColor={emphasized ? 'border-smu-navy/20' : undefined}>
      <DetailItem label={label}>
        <p className="text-[22px] font-extrabold text-smu-navy">{formatCreditsPrecise(value)} credits</p>
      </DetailItem>
    </Card>
  )
}

export default function PortfolioPage() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [rows, setRows] = useState<PositionRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    getMyPortfolio()
      .then(async found => {
        setPortfolio(found)
        const markets = await loadMarkets(found.positions)
        setRows(found.positions.map(p => toRow(p, markets)))
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  return (
    <AppLayout width="max-w-[1100px]">
      <PageTitle className="mb-8">Portfolio</PageTitle>

      {loading && <p className="text-sm text-muted">Loading portfolio…</p>}

      {error && <p role="alert" className="text-sm text-danger">Failed to load portfolio. Please try again.</p>}

      {!loading && !error && portfolio && (
        <>
          <div className="mb-6 grid grid-cols-3 gap-4">
            <StatCard label="Cash" value={portfolio.balance} />
            <StatCard label="Positions value" value={portfolio.positions_value} />
            <StatCard label="Net worth" value={portfolio.net_worth} emphasized />
          </div>

          {rows.length === 0 && (
            <p className="text-sm text-muted">No open positions. Visit the markets page to start trading.</p>
          )}

          {rows.length > 0 && (
            <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-line text-xs font-semibold tracking-[0.5px] text-subtle uppercase">
                    <HeaderCell>Market</HeaderCell>
                    <HeaderCell>Outcome</HeaderCell>
                    <HeaderCell align="right">Quantity</HeaderCell>
                    <HeaderCell align="right">Avg. entry</HeaderCell>
                    <HeaderCell align="right">Cost basis</HeaderCell>
                    <HeaderCell align="right">Price</HeaderCell>
                    <HeaderCell align="right">Value</HeaderCell>
                    <HeaderCell align="right">Unrealized P&L</HeaderCell>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(row => (
                    <tr key={`${row.market_id}:${row.outcome_id}`} className="border-b border-line last:border-0">
                      <td className="px-5 py-3">
                        <Link to={`/markets/${row.market_id}`} className="font-medium text-smu-navy hover:underline">
                          {row.question}
                        </Link>
                      </td>
                      <td className="px-5 py-3 text-muted">{row.outcomeLabel}</td>
                      <NumberCell>{row.quantity}</NumberCell>
                      <NumberCell>{row.average_entry_price}</NumberCell>
                      <NumberCell>{formatCreditsPrecise(row.cost_basis)}</NumberCell>
                      <NumberCell>{row.price}</NumberCell>
                      <NumberCell>{formatCreditsPrecise(row.value)}</NumberCell>
                      <td className="px-5 py-3 text-right"><PnlCell pnl={row.unrealized_pnl} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </AppLayout>
  )
}
