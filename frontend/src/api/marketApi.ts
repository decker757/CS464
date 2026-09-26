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
