import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMyPortfolio, type Portfolio, type Position } from '../api/ledgerApi'
import { getMarket, type PublicMarketDetail } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import Card from '../components/ui/Card'
import PageTitle from '../components/ui/PageTitle'
import { formatCredits, formatCreditsPrecise, unsignedCredits } from '../utils/formatCredits'

interface PositionRow extends Position {
  question: string
  outcomeLabel: string
}

// Markets, keyed by id, for every distinct market a position names — one
// fetch per market held, not per position (ledger-service.md: the portfolio
// carries ids only, and labels are the frontend's own join).
async function loadMarkets(positions: Position[]): Promise<Map<string, PublicMarketDetail>> {
  const ids = [...new Set(positions.map(p => p.market_id))]
  const markets = await Promise.all(ids.map(getMarket))
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

function PnlCell({ pnl }: { pnl: string }) {
  const isLoss = pnl.startsWith('-')
  return (
    <span className={isLoss ? 'text-danger' : 'text-success'}>
      {formatCreditsPrecise(pnl, { showSign: true })}
    </span>
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
            <Card className="px-6 py-5">
              <p className="mb-1 text-xs font-semibold tracking-[0.5px] text-subtle uppercase">Cash</p>
              <p className="text-[22px] font-extrabold text-smu-navy">{formatCredits(portfolio.balance)} credits</p>
            </Card>
            <Card className="px-6 py-5">
              <p className="mb-1 text-xs font-semibold tracking-[0.5px] text-subtle uppercase">Positions value</p>
              <p className="text-[22px] font-extrabold text-smu-navy">{formatCredits(portfolio.positions_value)} credits</p>
            </Card>
            <Card className="px-6 py-5" borderColor="border-smu-navy/20">
              <p className="mb-1 text-xs font-semibold tracking-[0.5px] text-subtle uppercase">Net worth</p>
              <p className="text-[22px] font-extrabold text-smu-navy">{formatCredits(portfolio.net_worth)} credits</p>
            </Card>
          </div>

          {rows.length === 0 && (
            <p className="text-sm text-muted">No open positions. Visit the markets page to start trading.</p>
          )}

          {rows.length > 0 && (
            <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-line text-xs font-semibold tracking-[0.5px] text-subtle uppercase">
                    <th className="px-5 py-3">Market</th>
                    <th className="px-5 py-3">Outcome</th>
                    <th className="px-5 py-3 text-right">Quantity</th>
                    <th className="px-5 py-3 text-right">Avg. entry</th>
                    <th className="px-5 py-3 text-right">Price</th>
                    <th className="px-5 py-3 text-right">Value</th>
                    <th className="px-5 py-3 text-right">Unrealized P&L</th>
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
                      <td className="px-5 py-3 text-right text-smu-navy">{row.quantity}</td>
                      <td className="px-5 py-3 text-right text-smu-navy">{row.average_entry_price}</td>
                      <td className="px-5 py-3 text-right text-smu-navy">{row.price}</td>
                      <td className="px-5 py-3 text-right text-smu-navy">{formatCreditsPrecise(unsignedCredits(row.value))}</td>
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
