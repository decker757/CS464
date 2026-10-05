import api from './axios'
import { fetchAllPages } from './paging'

// Same pattern as marketApi.ts and ledgerApi.ts: an absolute URL on the
// shared `api` instance, so the refresh interceptor and withCredentials come along.
const AUDIT_BASE = import.meta.env.VITE_AUDIT_API_URL ?? 'http://localhost:8002'

export interface AdminAction {
  id: string
  occurred_at: string
  actor_id: string
  actor_username: string
  actor_role: string
  action_type: string
  target_type: string
  target_id: string
  target_label: string
  reason: string | null
  context: Record<string, unknown>
  source_service: string
}

export interface AdminActionPage {
  actions: AdminAction[]
  next_cursor: string | null
  has_more: boolean
}

/** One page of the admin action log, newest first (audit-service.md). */
export async function listActions(params?: { action_type?: string; actor_id?: string; cursor?: string; limit?: number }): Promise<AdminActionPage> {
  const res = await api.get<AdminActionPage>(`${AUDIT_BASE}/audit/actions`, { params })
  return res.data
}

/** Every entry of one action_type, walking every page (audit-service.md's keyset paging). */
export async function listAllActions(actionType: string): Promise<AdminAction[]> {
  return fetchAllPages(async cursor => {
    const page = await listActions({ action_type: actionType, cursor, limit: 200 })
    return { items: page.actions, nextCursor: page.next_cursor }
  })
}
