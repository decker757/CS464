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

// The newest entry per target_id, for an action type where only the latest
// one can still be live — a market.outcome_proposed superseded by a decision,
// or a market.outcome_approved superseded by a send-back, is never the one a
// screen should act on. Shared by the proposal and the approval reads below,
// since both need exactly this shape (market-service.md's approval-screen
// notes, point 8, and dispute-screen notes, point 3).
export function latestActionPerTarget(entries: AdminAction[]): Map<string, AdminAction> {
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
