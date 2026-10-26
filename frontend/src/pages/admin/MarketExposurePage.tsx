import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getMarketExposure, type MarketExposure, type OutcomeExposure } from '../../api/ledgerApi'
import AppLayout from '../../components/layout/AppLayout'
import MarketExposureTable from '../../components/markets/MarketExposureTable'
import BackLink from '../../components/ui/BackLink'
import Button from '../../components/ui/Button'
import DetailItem from '../../components/ui/DetailItem'
import PageTitle from '../../components/ui/PageTitle'
import SectionCard from '../../components/ui/SectionCard'
import { useMarketDetail } from '../../hooks/useMarketDetail'
import { compareCredits, formatCreditsPrecise, isNegativeCredits, isPlainCredits, signedAmountColor, unsignedCredits } from '../../utils/formatCredits'

interface WorstCase {
  outcomeId: string
  /** The server's own `residue_if_wins` for that outcome, untouched. */
  residue: string
  /** True when the pool cannot cover that outcome's payout. */
  isShort: boolean
}

/** The outcome that costs the platform most if it wins: the smallest
 * `residue_if_wins`, since the pool is the same whichever outcome wins. There
 * is no field for it — docs/api/ledger-service.md derives it from `outcomes`
 * like every other figure in this service. Choosing is a comparison and
 * nothing is computed: `compareCredits` orders the ledger's own strings, and
 * the one that wins is displayed as it arrived. Null once the market has
 * settled, where every residue is null, and on a market with no book yet. */
function worstCaseOutcome(outcomes: OutcomeExposure[]): WorstCase | null {
  let worst: WorstCase | null = null
  for (const outcome of outcomes) {
    const residue = outcome.residue_if_wins
    // Null once the market has settled. A value that is not a plain decimal
    // is dropped the same way: it would show as "—", and naming an outcome
    // the worst case while being unable to say by how much is worse than
    // naming the worst of the figures that can be read.
    if (residue === null || !isPlainCredits(residue)) continue
    if (worst === null || compareCredits(residue, worst.residue) < 0) {
      worst = { outcomeId: outcome.outcome_id, residue, isShort: isNegativeCredits(residue) }
    }
  }
  return worst
}

/** The worst case in words: what the platform is on the hook for if that
 * outcome wins, or what the pool keeps when it covers every payout. The
 * figure is the ledger's own string, with the minus sign dropped on a
 * shortfall because "tops up" already says which way it points. */
function worstCaseSentence(worstCase: WorstCase, label: string): string {
  if (worstCase.isShort) {
    return `If ${label} wins, the platform tops up ${formatCreditsPrecise(unsignedCredits(worstCase.residue))}.`
  }
  return `If ${label} wins, the pool keeps ${formatCreditsPrecise(worstCase.residue)}.`
}

/** The worst case's colour is the residue column's own `signedAmountColor`
 * (#418), so the same residue is the same colour in the line and in the cell
 * below it: red short, green over, neutral at zero. The weight is this line's
 * alone, and a shortfall keeps it because it is the figure an admin resolving
 * the market has to act on. */
function worstCaseClassName(worstCase: WorstCase): string {
  const color = signedAmountColor(worstCase.residue)
  return worstCase.isShort ? `font-semibold ${color}` : color
}

// [2.2] #55. Not scoped to the market's creator: knowing payout size before
// resolving is #6's whole story, which is oversight, not authorship — same
// reasoning as #8's price history and DecideOutcomePage.
export default function MarketExposurePage() {
  const { id } = useParams<{ id: string }>()

  const { market, hasError: marketError, outcomeLabels } = useMarketDetail(id)

  const [exposure, setExposure] = useState<MarketExposure | null>(null)
  const [exposureLoading, setExposureLoading] = useState(true)
  const [exposureError, setExposureError] = useState(false)
  // Exposure moves with every trade, so the figures on screen are only ever
  // as good as the moment they were read (#361): say when that was, and let
  // the admin read it again before resolving.
  const [readAt, setReadAt] = useState<Date | null>(null)
  const [readCount, setReadCount] = useState(0)

  useEffect(() => {
    if (!id) return
    let ignored = false
    getMarketExposure(id)
      .then(found => {
        if (ignored) return
        setExposure(found)
        setReadAt(new Date())
      })
      .catch(() => { if (!ignored) setExposureError(true) })
      .finally(() => { if (!ignored) setExposureLoading(false) })
    return () => { ignored = true }
  }, [id, readCount])

  // The read is asked for here rather than in the effect, so the button is
  // disabled and any earlier failure cleared from the click that caused it.
  function handleRefresh() {
    setExposureLoading(true)
    setExposureError(false)
    setReadCount(count => count + 1)
  }

  const worstCase = exposure === null ? null : worstCaseOutcome(exposure.outcomes)
  const worstCaseLabel = worstCase === null ? '' : outcomeLabels.get(worstCase.outcomeId) ?? worstCase.outcomeId
  const winningOutcomeId = exposure?.winning_outcome_id ?? null
  const winnerLabel = winningOutcomeId === null ? null : outcomeLabels.get(winningOutcomeId) ?? winningOutcomeId

  return (
    <AppLayout width="max-w-[900px]">
      <BackLink to="/admin/markets" label="All Markets" className="mb-4 block" />

      {marketError && <p role="alert" className="text-sm text-danger">Failed to load this market.</p>}

      {market && (
        <>
          <PageTitle className="mb-4">{market.question}</PageTitle>

          <div className="mb-8 flex flex-wrap items-center gap-3">
            <p className="text-sm text-muted">
              {readAt === null
                ? 'Not read yet.'
                : `Read at ${readAt.toLocaleTimeString('en-SG')} · state version ${exposure?.state_version ?? '—'}`}
            </p>
            {/* Disabled while a read is on its way, so a double click asks once. */}
            <Button variant="outline" size="xs" onClick={handleRefresh} disabled={exposureLoading}>
              {exposureLoading ? 'Refreshing…' : 'Refresh'}
            </Button>
          </div>

          {exposureLoading && exposure === null && <p className="text-sm text-muted">Loading exposure…</p>}
          {exposureError && <p role="alert" className="text-sm text-danger">Failed to load exposure.</p>}

          {exposure && (
            <>
              {exposure.result === 'settled' && (
                <p className="mb-6 text-sm text-muted">
                  This market has settled{winnerLabel === null ? '' : `; ${winnerLabel} won`} and the pool has been paid out.
                </p>
              )}
              {/* A void empties the pool as settlement does, so there is no
                  live pool to draw (#526). */}
              {exposure.result === 'voided' && (
                <p className="mb-6 text-sm text-muted">
                  This market was voided, so it has no winner, and every holder has been refunded.
                </p>
              )}
              {exposure.result === null && (
                <SectionCard title="Pool" className="mb-6">
                  <div className="grid gap-5 sm:grid-cols-2">
                    <DetailItem label="Seed plus every buy, minus every sell">
                      <p className="text-smu-navy">{exposure.pool === null ? '—' : formatCreditsPrecise(exposure.pool)}</p>
                    </DetailItem>
                    {worstCase && (
                      <DetailItem label="Worst case">
                        <p className={worstCaseClassName(worstCase)}>
                          {worstCaseSentence(worstCase, worstCaseLabel)}
                        </p>
                      </DetailItem>
                    )}
                  </div>
                  {exposure.payout_exceeds_pool && worstCase && (
                    <p role="alert" className="mt-4 text-sm font-semibold text-danger">
                      The payout for {worstCaseLabel} exceeds the pool.
                    </p>
                  )}
                </SectionCard>
              )}

              {exposure.outcomes.length === 0 ? (
                <p className="text-sm text-muted">No trades yet, so there is no exposure to show.</p>
              ) : (
                <MarketExposureTable outcomes={exposure.outcomes} outcomeLabels={outcomeLabels} />
              )}
            </>
          )}
        </>
      )}
    </AppLayout>
  )
}
