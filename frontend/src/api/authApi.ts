import api from './axios'

export interface User {
  id: string
  username: string
  email: string
  role: 'trader' | 'admin'
  created_at: string
}

export interface Credentials {
  identifier: string
  password: string
}

export interface Registration {
  username: string
  email: string
  password: string
}

export async function login(credentials: Credentials): Promise<User> {
  const res = await api.post<{ user: User }>('/auth/login', credentials)
  return res.data.user
}

export async function register(registration: Registration): Promise<User> {
  const res = await api.post<{ user: User }>('/auth/register', registration)
  return res.data.user
}

export async function getCurrentUser(): Promise<User> {
  const res = await api.get<User>('/auth/me')
  return res.data
}

export async function logout(): Promise<void> {
  await api.post('/auth/logout')
}

export interface AdminUser extends User {
  is_suspended: boolean
}

export interface AdminUserPage {
  users: AdminUser[]
  next_cursor: string | null
  has_more: boolean
}

/**
 * One page of accounts an administrator can search by username or email
 * fragment ([4.1] #13, auth-service.md). Blank or omitted `q` lists
 * everybody, newest registration first — the screen opens on a list rather
 * than an empty state waiting to be typed into. `role` and `is_suspended`
 * narrow it further (#479); an omitted one filters nothing. A cursor carries
 * a position, not the filters, so send all three again with it.
 */
export async function listUsers(params?: {
  q?: string
  role?: User['role']
  is_suspended?: boolean
  cursor?: string
}): Promise<AdminUserPage> {
  const res = await api.get<AdminUserPage>('/admin/users', { params })
  return res.data
}

/** One account as a trade table names it: exactly `id` and `username`
 * (auth-service.md). No email, no role and no `is_suspended` — those stay on
 * `GET /admin/users`, which `AdminUser` above describes. */
export interface UserName {
  id: string
  username: string
}

/**
 * The usernames for a page of user ids, in one request ([2.4] #273,
 * auth-service.md). The ledger's trade feed carries a `user_id` and no
 * username, because the ledger holds no user table (ADR 0003), so the
 * administrator's browser joins the names in here.
 *
 * Send between 1 and 200 ids — one trades page's worth, which is what the
 * ledger clamps a page at. An id that names no user is left out rather than
 * refused, a duplicate is answered once, and no order is promised, so the
 * answer is keyed by `id` (useUsernames does that).
 */
export async function lookupUsernames(userIds: string[]): Promise<UserName[]> {
  const res = await api.post<{ users: UserName[] }>('/admin/users/lookup', { user_ids: userIds })
  return res.data.users
}

export interface SuspensionResult {
  user: AdminUser
  // How long a token already issued stays honoured elsewhere, never zero
  // (auth-service.md). Show it, not "immediately".
  takes_effect_within_seconds: number
}

/**
 * Suspends or unsuspends an account, with a reason required both ways
 * ([4.2] #14, auth-service.md). `suspended` is the state the account should
 * end up in, not an action — asking for the state it is already in changes
 * nothing and is not an error, so this needs no prior read of the account's
 * current state to call correctly.
 */
export async function setSuspension(userId: string, suspended: boolean, reason: string): Promise<SuspensionResult> {
  const res = await api.patch<SuspensionResult>(`/admin/users/${userId}/suspension`, { suspended, reason })
  return res.data
}
