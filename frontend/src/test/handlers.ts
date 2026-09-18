import { http, HttpResponse } from 'msw'

const BASE = 'http://localhost:8000'

export const mockUser = {
  id: '1',
  username: 'alice',
  email: 'alice@smu.edu.sg',
  role: 'trader' as const,
}

export const handlers = [
  http.get(`${BASE}/auth/me`, () =>
    HttpResponse.json(null, { status: 401 })
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
