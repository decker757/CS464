import { http, HttpResponse } from 'msw'

const BASE = 'http://localhost:8000'

export const mockUser = {
  id: '1',
  username: 'alice',
  email: 'alice@smu.edu.sg',
  role: 'trader' as const,
  created_at: '2026-01-01',
}

export const handlers = [
  // Real server sends { error: { code: 'invalid_token' } } for unauthenticated requests
  http.get(`${BASE}/auth/me`, () =>
    HttpResponse.json(
      { error: { code: 'invalid_token', message: 'Token expired' } },
      { status: 401 },
    )
  ),

  // Default: refresh also fails (no session). Code differs from invalid_token so
  // the interceptor doesn't loop trying to refresh the refresh.
  http.post(`${BASE}/auth/refresh`, () =>
    HttpResponse.json(
      { error: { code: 'session_expired', message: 'Session expired' } },
      { status: 401 },
    )
  ),

  http.post(`${BASE}/auth/register`, () =>
    HttpResponse.json({ user: mockUser }, { status: 201 })
  ),

  http.post(`${BASE}/auth/login`, () =>
    HttpResponse.json({ user: mockUser })
  ),

  http.post(`${BASE}/auth/logout`, () =>
    new HttpResponse(null, { status: 204 })
  ),
]
