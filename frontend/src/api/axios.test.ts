import { beforeAll, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import api from './axios'

// jsdom doesn't implement navigator.locks — provide a minimal synchronous mock
beforeAll(() => {
  Object.defineProperty(navigator, 'locks', {
    value: {
      request: (_name: string, callback: () => Promise<unknown>) => callback(),
    },
    configurable: true,
    writable: true,
  })
})

const invalidToken = (status = 401) =>
  HttpResponse.json(
    { error: { code: 'invalid_token', message: 'Token expired' } },
    { status },
  )

describe('axios refresh interceptor', () => {
  it('retries the original request after a successful /auth/refresh', async () => {
    let callCount = 0
    server.use(
      http.get('http://localhost:8000/api/probe', () => {
        callCount++
        return callCount === 1 ? invalidToken() : HttpResponse.json({ ok: true })
      }),
      http.post('http://localhost:8000/auth/refresh', () =>
        new HttpResponse(null, { status: 204 }),
      ),
    )

    const res = await api.get('/api/probe')
    expect(res.data).toEqual({ ok: true })
    expect(callCount).toBe(2)
  })

  it('rejects when /auth/refresh fails, without redirecting', async () => {
    server.use(
      http.get('http://localhost:8000/api/probe', () => invalidToken()),
      // default /auth/refresh handler returns session_expired (already in handlers.ts)
    )

    await expect(api.get('/api/probe')).rejects.toThrow()
    // ProtectedRoute is responsible for redirecting — the interceptor must not
    // touch window.location, otherwise logged-out visitors loop forever
    expect(window.location.pathname).not.toBe('/login')
  })

  it('does not retry /auth/refresh itself when it returns invalid_token', async () => {
    let refreshCallCount = 0
    server.use(
      http.post('http://localhost:8000/auth/refresh', () => {
        refreshCallCount++
        return invalidToken()
      }),
    )

    await expect(api.post('/auth/refresh')).rejects.toThrow()
    expect(refreshCallCount).toBe(1)
  })
})
