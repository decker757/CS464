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
 * than an empty state waiting to be typed into.
 */
export async function listUsers(params?: { q?: string; cursor?: string }): Promise<AdminUserPage> {
  const res = await api.get<AdminUserPage>('/admin/users', { params })
  return res.data
}
