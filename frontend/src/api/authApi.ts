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
