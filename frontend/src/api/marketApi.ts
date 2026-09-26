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
  status: string
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
