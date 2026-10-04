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
      { error: { code: 'invalid_token', message: 'Not authenticated.' } },
      { status: 401 },
    )
  ),

  // Default: refresh also fails (no session). Uses the same invalid_token code
  // as the real server — the loop is blocked by the isRefreshRequest check in
  // axios.ts, not by using a different error code.
  http.post(`${BASE}/auth/refresh`, () =>
    HttpResponse.json(
      { error: { code: 'invalid_token', message: 'Not authenticated.' } },
      { status: 401 },
    )
  ),

  http.post(`${BASE}/auth/register`, () =>
    HttpResponse.json({ user: mockUser }, { status: 201 })
  ),

  http.post(`${BASE}/auth/login`, () =>
    HttpResponse.json({ user: mockUser })
  ),

  // 200 with a body, not 204 — docs/api/auth-service.md says this endpoint is
  // deliberately unauthenticated and *always* returns 200 { message }.
  http.post(`${BASE}/auth/logout`, () =>
    HttpResponse.json({ message: 'Logged out.' })
  ),
]
