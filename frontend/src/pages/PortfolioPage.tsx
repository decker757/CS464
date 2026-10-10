import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMyPortfolio, type Portfolio, type Position } from '../api/ledgerApi'
import type { PublicMarketDetail } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import Card from '../components/ui/Card'
import DetailItem from '../components/ui/DetailItem'
import PageTitle from '../components/ui/PageTitle'
import { HeaderCell, NumberCell } from '../components/ui/Table'
import { formatCreditsPrecise, signedAmountColor } from '../utils/formatCredits'
import { formatPrice } from '../utils/formatPrice'
import { labelsFor, loadMarketsById } from '../utils/marketLabels'

interface PositionRow extends Position {
  question: string
  outcomeLabel: string
}

function toRow(position: Position, markets: Map<string, PublicMarketDetail>): PositionRow {
  return { ...position, ...labelsFor(position.market_id, position.outcome_id, markets) }
}

function PnlCell({ pnl }: { pnl: string }) {
  return (
    <span className={signedAmountColor(pnl)}>
      {formatCreditsPrecise(pnl, { showSign: true })}
    </span>
  )
}

// Shown at full precision so the three cards add up.
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
        const markets = await loadMarketsById(found.positions.map(p => p.market_id))
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
                      <NumberCell>{formatPrice(row.price)}</NumberCell>
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
