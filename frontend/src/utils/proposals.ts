import type { AdminAction } from '../api/auditApi'

export interface PendingProposal {
  marketId: string
  question: string
  proposalId: string | null
  proposedById: string
  proposedByUsername: string
  proposedAt: string
  winningOutcome: string
  evidenceUrl: string | null
  evidenceNote: string | null
}

// There is no read for another administrator's market by id (market-service.md,
// "Notes for the approval screen", point 8), so a pending market's proposer and
// evidence come from the audit log instead of a market fetch. Exactly one
// proposal is ever live on a pending_resolution market — a second proposal
// attempt is refused with 409 market_pending_resolution — so the latest
// market.outcome_proposed entry per market is the one waiting on a decision.
export function latestProposalPerMarket(entries: AdminAction[]): Map<string, AdminAction> {
  const latest = new Map<string, AdminAction>()
  for (const entry of entries) {
    const current = latest.get(entry.target_id)
    if (!current || new Date(entry.occurred_at) > new Date(current.occurred_at)) {
      latest.set(entry.target_id, entry)
    }
  }
  return latest
}

export function toPendingProposal(market: { id: string; question: string }, entry: AdminAction): PendingProposal {
  const context = entry.context as {
    proposal_id?: string | null
    winning_outcome?: string
    evidence_url?: string | null
    evidence_note?: string | null
  }
  return {
    marketId: market.id,
    question: market.question,
    proposalId: context.proposal_id ?? null,
    proposedById: entry.actor_id,
    proposedByUsername: entry.actor_username,
    proposedAt: entry.occurred_at,
    winningOutcome: context.winning_outcome ?? '',
    evidenceUrl: context.evidence_url ?? null,
    evidenceNote: context.evidence_note ?? null,
  }
}
