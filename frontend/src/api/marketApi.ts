import api from './axios'

// Market service lives on a different port. Passing an absolute URL to the
// shared `api` instance means it inherits the same refresh interceptor and
// withCredentials config without duplicating the setup.
const MARKET_BASE = import.meta.env.VITE_MARKET_API_URL ?? 'http://localhost:8001'

export interface PublicMarketSummary {
  id: string
  status: 'open' | 'closed' | 'pending_resolution' | 'approved'
  question: string
  close_time: string
}

export async function listMarkets(params?: { status?: string; q?: string }): Promise<PublicMarketSummary[]> {
  const res = await api.get<{ markets: PublicMarketSummary[] }>(`${MARKET_BASE}/public/markets`, { params })
  return res.data.markets
}

export interface PublicOutcome {
  id: string
  position: number
  label: string
}

export interface PublicSource {
  id: string
  position: number
  url: string
  label: string | null
}

export interface PublicMarketDetail {
  id: string
  status: 'open' | 'closed' | 'pending_resolution' | 'approved'
  question: string
  description: string | null
  outcomes: PublicOutcome[]
  close_time: string
  resolution_time: string
  resolution_criteria: string
  resolution_sources: PublicSource[]
  liquidity_b: string
  seed_subsidy: string
  published_at: string
  proposed_outcome_id: string | null
}

export async function getMarket(id: string): Promise<PublicMarketDetail> {
  const res = await api.get<PublicMarketDetail>(`${MARKET_BASE}/public/markets/${id}`)
  return res.data
}

export interface OutcomeOut {
  id: string
  position: number
  label: string
  initial_price: number | null
}

export interface SourceOut {
  id: string
  position: number
  url: string
  label: string | null
}

export interface MarketOut {
  id: string
  status: 'draft' | 'submitted' | 'open' | 'closed' | 'pending_resolution' | 'approved'
  question: string | null
  description: string | null
  outcomes: OutcomeOut[]
  close_time: string | null
  resolution_time: string | null
  resolution_criteria: string | null
  resolution_sources: SourceOut[]
  liquidity_b: number | null
  seed_subsidy: number | null
  max_platform_loss: number | null
  // Set together once an outcome is proposed ([3.1] #9); null together until then.
  proposal_id: string | null
  proposed_outcome_id: string | null
  proposed_by_id: string | null
  proposed_by_username: string | null
  proposed_at: string | null
  proposal_evidence_url: string | null
  proposal_evidence_note: string | null
  // Set together once the proposal is approved ([3.2] #10); null together until then.
  approved_by_id: string | null
  approved_by_username: string | null
  approved_at: string | null
}

export interface BlockingHint {
  field: string
  message: string
}

export interface SaveMarketResponse {
  market: MarketOut
  blocking_submission: BlockingHint[]
}

export interface SaveMarketRequest {
  draft_key: string
  status: 'draft' | 'submitted'
  question?: string
  description?: string
  outcomes?: { label: string }[]
  close_time?: string
  resolution_time?: string
  resolution_criteria?: string
  resolution_sources?: { url: string; label?: string }[]
  liquidity_b?: number
  seed_subsidy?: number
}

export async function saveMarket(data: SaveMarketRequest): Promise<SaveMarketResponse> {
  const res = await api.post<SaveMarketResponse>(`${MARKET_BASE}/markets`, data)
  return res.data
}

export async function publishMarket(id: string): Promise<MarketOut> {
  const res = await api.post<MarketOut>(`${MARKET_BASE}/markets/${id}/publish`)
  return res.data
}

export interface MarketSummaryOut {
  id: string
  draft_key: string
  status: 'draft' | 'submitted' | 'open' | 'closed' | 'pending_resolution' | 'approved'
  question: string | null
  close_time: string | null
  updated_at: string
}

/** Every market the calling administrator owns, most recently updated first (market-service.md, GET /markets). */
export async function listMyMarkets(): Promise<MarketSummaryOut[]> {
  const res = await api.get<{ markets: MarketSummaryOut[] }>(`${MARKET_BASE}/markets`)
  return res.data.markets
}

export interface MarketOverviewRow {
  id: string
  creator_id: string
  // Derived from the clock: past its close_time reads `closed` before the sweep writes it.
  status: MarketSummaryOut['status']
  question: string | null
  close_time: string | null
}

export interface MarketOverview {
  markets: MarketOverviewRow[]
  counts: Record<MarketSummaryOut['status'], number>
}

/**
 * Every market the calling administrator can see — all published ones plus
 * their own drafts and submissions — soonest close first, with a count per
 * status read at the same instant ([2.1] #5, market-service.md, GET /markets/overview).
 */
export async function getMarketOverview(): Promise<MarketOverview> {
  const res = await api.get<MarketOverview>(`${MARKET_BASE}/markets/overview`)
  return res.data
}

/** One of the calling administrator's own markets, with the raw status column (market-service.md, GET /markets/{id}). */
export async function getMyMarket(id: string): Promise<MarketOut> {
  const res = await api.get<MarketOut>(`${MARKET_BASE}/markets/${id}`)
  return res.data
}

export interface ProposeOutcomeRequest {
  winning_outcome_id: string
  evidence_url?: string
  evidence_note?: string
}

/** Names the winner of a closed market, moving it to pending_resolution ([3.1] #9). */
export async function proposeOutcome(id: string, data: ProposeOutcomeRequest): Promise<MarketOut> {
  const res = await api.post<MarketOut>(`${MARKET_BASE}/markets/${id}/propose-outcome`, data)
  return res.data
}

/**
 * Agrees with a pending proposal, moving the market to approved ([3.2] #10).
 * `proposalId` must be the id of the proposal the reviewer read, null included
 * — never refetched at click time (market-service.md's approval-screen notes).
 */
export async function approveOutcome(id: string, proposalId: string | null): Promise<MarketOut> {
  const res = await api.post<MarketOut>(`${MARKET_BASE}/markets/${id}/approve-outcome`, { proposal_id: proposalId })
  return res.data
}

/** Sends a pending proposal back to closed, with a reason ([3.2] #10). Same proposalId rule as approveOutcome. */
export async function rejectOutcome(id: string, proposalId: string | null, reason: string): Promise<MarketOut> {
  const res = await api.post<MarketOut>(`${MARKET_BASE}/markets/${id}/reject-outcome`, { proposal_id: proposalId, reason })
  return res.data
}
