import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { listAllActions } from '../../api/auditApi'
import { listAllMarkets } from '../../api/marketApi'
import { useAuth } from '../../context/AuthContext'
import AppLayout from '../../components/layout/AppLayout'
import Card from '../../components/ui/Card'
import PageTitle from '../../components/ui/PageTitle'
import { buttonClass } from '../../components/ui/buttonClass'
import { latestProposalPerMarket, toPendingProposal, type PendingProposal } from '../../utils/proposals'

export default function ProposalsPage() {
  const { user } = useAuth()
  const [proposals, setProposals] = useState<PendingProposal[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    Promise.all([
      listAllMarkets({ status: 'pending_resolution' }),
      listAllActions('market.outcome_proposed'),
    ])
      .then(([markets, entries]) => {
        const latest = latestProposalPerMarket(entries)
        const found = markets
          .map(market => {
            const entry = latest.get(market.id)
            return entry ? toPendingProposal(market, entry) : null
          })
          .filter((p): p is PendingProposal => p !== null)
        setProposals(found)
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  const sorted = useMemo(
    () => [...proposals].sort((a, b) => new Date(a.proposedAt).getTime() - new Date(b.proposedAt).getTime()),
    [proposals],
  )

  return (
    <AppLayout width="max-w-[1000px]">
      <PageTitle className="mb-2">Proposals Awaiting Decision</PageTitle>
      <p className="mb-8 text-sm text-muted">Every market proposed for resolution, oldest first.</p>

      {loading && <p className="text-sm text-muted">Loading proposals…</p>}

      {error && <p role="alert" className="text-sm text-danger">Failed to load proposals. Please try again.</p>}

      {!loading && !error && sorted.length === 0 && (
        <p className="text-sm text-muted">No proposals are waiting on a decision.</p>
      )}

      {!loading && !error && sorted.length > 0 && (
        <div className="flex flex-col gap-3">
          {sorted.map(proposal => {
            const isOwnProposal = proposal.proposedById === user?.id
            return (
              <Card key={proposal.marketId} className="flex items-center justify-between gap-4 px-6 py-4">
                <div className="min-w-0">
                  <p className="truncate text-[15px] font-semibold text-smu-navy">{proposal.question}</p>
                  <p className="mt-1 text-xs text-subtle">
                    Proposed by {proposal.proposedByUsername} — winner: {proposal.winningOutcome}
                  </p>
                </div>
                {isOwnProposal ? (
                  <span className="shrink-0 text-xs text-subtle">You proposed this</span>
                ) : (
                  <Link to={`/admin/markets/${proposal.marketId}/decide-outcome`} className={`shrink-0 ${buttonClass('outline', 'xs')}`}>
                    Review
                  </Link>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </AppLayout>
  )
}
