import { describe, expect, it } from 'vitest'
import type { AdminAction } from '../api/auditApi'
import { latestProposalPerMarket, toPendingProposal } from './proposals'

function proposedEntry(overrides: Partial<AdminAction> = {}): AdminAction {
  return {
    id: 'a1',
    occurred_at: '2026-01-01T00:00:00Z',
    actor_id: 'admin-1',
    actor_username: 'ernest_t',
    actor_role: 'admin',
    action_type: 'market.outcome_proposed',
    target_type: 'market',
    target_id: 'mkt-1',
    target_label: 'Will it rain?',
    reason: null,
    context: {
      proposal_id: 'prop-1',
      winning_outcome_id: 'o-yes',
      winning_outcome: 'Yes',
      evidence_url: 'https://example.com',
      evidence_note: 'A note.',
    },
    source_service: 'market_service',
    ...overrides,
  }
}

describe('latestProposalPerMarket', () => {
  it('picks the most recent entry per market', () => {
    const older = proposedEntry({ id: 'a1', occurred_at: '2026-01-01T00:00:00Z', context: { proposal_id: 'prop-1' } })
    const newer = proposedEntry({ id: 'a2', occurred_at: '2026-01-02T00:00:00Z', context: { proposal_id: 'prop-2' } })
    const result = latestProposalPerMarket([older, newer])
    expect(result.get('mkt-1')?.id).toBe('a2')
  })

  it('keeps entries for different markets separate', () => {
    const first = proposedEntry({ id: 'a1', target_id: 'mkt-1' })
    const second = proposedEntry({ id: 'a2', target_id: 'mkt-2' })
    const result = latestProposalPerMarket([first, second])
    expect(result.size).toBe(2)
    expect(result.get('mkt-1')?.id).toBe('a1')
    expect(result.get('mkt-2')?.id).toBe('a2')
  })

  it('is order-independent: the same result regardless of input order', () => {
    const older = proposedEntry({ id: 'a1', occurred_at: '2026-01-01T00:00:00Z' })
    const newer = proposedEntry({ id: 'a2', occurred_at: '2026-01-02T00:00:00Z' })
    expect(latestProposalPerMarket([newer, older]).get('mkt-1')?.id).toBe('a2')
    expect(latestProposalPerMarket([older, newer]).get('mkt-1')?.id).toBe('a2')
  })
})

describe('toPendingProposal', () => {
  it('maps an audit entry and market into a PendingProposal', () => {
    const entry = proposedEntry()
    const result = toPendingProposal({ id: 'mkt-1', question: 'Will it rain?' }, entry)
    expect(result).toEqual({
      marketId: 'mkt-1',
      question: 'Will it rain?',
      proposalId: 'prop-1',
      proposedById: 'admin-1',
      proposedByUsername: 'ernest_t',
      proposedAt: '2026-01-01T00:00:00Z',
      winningOutcome: 'Yes',
      evidenceUrl: 'https://example.com',
      evidenceNote: 'A note.',
    })
  })

  it('defaults proposalId, evidenceUrl and evidenceNote to null when the context omits them', () => {
    // A proposal made before proposal ids existed has no proposal_id key
    // (market-service.md); evidence can be URL-only or note-only.
    const entry = proposedEntry({ context: { winning_outcome: 'Yes', evidence_note: 'A note.' } })
    const result = toPendingProposal({ id: 'mkt-1', question: 'Will it rain?' }, entry)
    expect(result.proposalId).toBeNull()
    expect(result.evidenceUrl).toBeNull()
    expect(result.evidenceNote).toBe('A note.')
  })
})
