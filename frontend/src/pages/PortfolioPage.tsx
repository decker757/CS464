import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getMyPortfolio, type Portfolio, type Position } from '../api/ledgerApi'
import type { PublicMarketDetail } from '../api/marketApi'
import AppLayout from '../components/layout/AppLayout'
import Card from '../components/ui/Card'
import DetailItem from '../components/ui/DetailItem'
import PageTitle from '../components/ui/PageTitle'
import { HeaderCell, NumberCell, TableHeaderRow } from '../components/ui/Table'
import { buttonClass } from '../components/ui/buttonClass'
import { formatAveragePrice, formatCredits, formatCreditsPrecise, roundedCredits, signedAmountColor } from '../utils/formatCredits'
import { formatPrice } from '../utils/formatPrice'
import { labelsFor, loadMarketsById } from '../utils/marketLabels'

interface PositionRow extends Position {
  question: string
  outcomeLabel: string
}

interface StatCardProps {
  label: string
  value: string
  /** One of the two figures that answer "how am I doing": larger, and with
   * the border emphasis. Balance and positions value are the working behind
   * them, so they read as the smaller pair (#443). */
  headline?: boolean
  /** A figure whose direction is the point, like P&L: shown with its sign
   * and coloured by it, rather than in navy like a plain balance. */
  signed?: boolean
  children?: React.ReactNode
}

// A settled position is no longer valued: its price, value and P&L arrive as
// null, and are shown as a dash rather than as zero ([3.4] #12).
const NOT_VALUED = '—'

function toRow(position: Position, markets: Map<string, PublicMarketDetail>): PositionRow {
  return { ...position, ...labelsFor(position.market_id, position.outcome_id, markets) }
}

// A settled row has no P&L — it is no longer valued — so this cell shows
// what it paid instead: the result and, for the winner, the payout. Never
// recomputed and never rendered as a zero P&L ([3.4] #12). A voided market's
// row shows its refund the same way, "0.0000" included (#526).
function PnlCell({ pnl, result, payout }: { pnl: string | null; result: Position['result']; payout: string | null }) {
  if (result === 'worthless') return <span className="text-muted">Worthless</span>
  if (result === 'paid_out') return <span className="text-success">Paid out {formatCreditsPrecise(payout ?? '0')}</span>
  if (result === 'refunded') return <span className="text-muted">Refunded {formatCreditsPrecise(payout ?? '0')}</span>
  if (pnl === null) return <span className="text-muted">{NOT_VALUED}</span>

  return (
    <span className={signedAmountColor(pnl)}>
      {formatCreditsPrecise(pnl, { showSign: true })}
    </span>
  )
}

// The colour of a card figure reads off the figure as shown, not the value
// behind it: a card rounds to the cent, so an unrealized P&L of -0.0001 reads
// "0" and must come out neutral rather than red (#443). A position row, which
// shows all four places, still colours on the exact value.
function cardFigureColor(value: string): string {
  return signedAmountColor(roundedCredits(value))
}

// Realized and unrealized sit under the total they make up rather than being
// folded into it: one is already banked in the balance, the other is still
// riding on open positions, and those are different answers to "how am I
// doing" (ledger-service.md). Label and figure share one element, the way
// PnlCell writes "Paid out 5.0000". [B-1] #376
function PnlPart({ label, value }: { label: string; value: string }) {
  return (
    <span className={cardFigureColor(value)}>
      {label} {formatCredits(value, { showSign: true })}
    </span>
  )
}

// A card is read at a glance, so its figure is rounded to the cent and the
// unit is said once, beside it rather than inside it — four figures ending
// ".0000 credits" were most of what the eye saw (#443). The exact figures are
// in the table below, which is where the arithmetic is checked.
//
// A `signed` card reads its direction from the sign as well as the colour, so
// colour is never the only signal, and a figure that comes to zero is neutral
// and unsigned — an untouched grant is flat, not a loss. [B-1] #376
function StatCard({ label, value, headline = false, signed = false, children }: StatCardProps) {
  return (
    <Card className="px-6 py-5" borderColor={headline ? 'border-smu-navy/20' : undefined}>
      <DetailItem label={label}>
        <p className={signed ? cardFigureColor(value) : 'text-smu-navy'}>
          <span className={`font-extrabold tabular-nums ${headline ? 'text-[28px]' : 'text-[19px]'}`}>
            {formatCredits(value, { showSign: signed })}
          </span>{' '}
          <span className="text-xs font-semibold text-muted">credits</span>
        </p>
        {children}
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
          {/* Net worth and total P&L answer "how am I doing", so they are the
              pair a reader lands on; balance and positions value are the two
              figures they are made of and sit under them (#443). */}
          <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-4">
            <StatCard label="Net worth" value={portfolio.net_worth} headline>
              {/* What the two cards below add up to, in words, so the pair
                  reads as the working behind this figure. */}
              <p className="mt-2 text-xs font-semibold text-muted">Balance plus the value of your open positions</p>
            </StatCard>
            {/* The ledger sends total_pnl as its own field so that nothing
                here has to add two decimal strings together; it is rendered
                exactly as it arrives (ledger-service.md). */}
            <StatCard label="Total P&L" value={portfolio.total_pnl} headline signed>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs font-semibold">
                <PnlPart label="Realized" value={portfolio.realized_pnl} />
                <PnlPart label="Unrealized" value={portfolio.unrealized_pnl} />
              </div>
            </StatCard>
          </div>

          {/* "Balance" is the word the navbar's readout, the history table's
              running total and the ledger's own field all use. The card used
              to say "Cash", which read as some subset of credits (#443). */}
          <div className="mb-8 grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
            <StatCard label="Balance" value={portfolio.balance} />
            <StatCard label="Positions value" value={portfolio.positions_value} />
          </div>

          {/* The one person who sees this screen empty is the one who has
              never traded, so the next step is a control and not a sentence
              naming a page they then have to find (#443). */}
          {rows.length === 0 && (
            <Card className="px-6 py-10 text-center">
              <p className="text-sm text-muted">No open positions yet.</p>
              <Link to="/markets" className={`mt-4 ${buttonClass('primary', 'md')}`}>
                Browse markets
                <span aria-hidden="true" className="text-base font-bold text-smu-gold">→</span>
              </Link>
            </Card>
          )}

          {rows.length > 0 && (
            <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
              <table className="w-full text-left text-sm">
                <thead>
                  <TableHeaderRow>
                    <HeaderCell>Market</HeaderCell>
                    <HeaderCell>Outcome</HeaderCell>
                    <HeaderCell align="right">Quantity</HeaderCell>
                    <HeaderCell align="right">Avg. entry</HeaderCell>
                    <HeaderCell align="right">Cost basis</HeaderCell>
                    <HeaderCell align="right">Price</HeaderCell>
                    <HeaderCell align="right">Value</HeaderCell>
                    <HeaderCell align="right">Unrealized P&L</HeaderCell>
                  </TableHeaderRow>
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
                      <NumberCell>{formatAveragePrice(row.average_entry_price)}</NumberCell>
                      <NumberCell>{formatCreditsPrecise(row.cost_basis)}</NumberCell>
                      <NumberCell>{row.price === null ? NOT_VALUED : formatPrice(row.price)}</NumberCell>
                      <NumberCell>{row.value === null ? NOT_VALUED : formatCreditsPrecise(row.value)}</NumberCell>
                      <td className="px-5 py-3 text-right"><PnlCell pnl={row.unrealized_pnl} result={row.result} payout={row.payout} /></td>
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
