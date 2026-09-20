import { afterEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import api from './axios'

// navigator.locks mock lives in src/test/setup.ts and applies to every file

const invalidToken = (status = 401) =>
  HttpResponse.json(
    { error: { code: 'invalid_token', message: 'Not authenticated.' } },
    { status },
  )

describe('axios refresh interceptor', () => {
  afterEach(() => vi.unstubAllGlobals())

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
    const location = { ...window.location, href: 'http://localhost:3000/' }
    vi.stubGlobal('location', location)

    server.use(
      http.get('http://localhost:8000/api/probe', () => invalidToken()),
      // default /auth/refresh handler returns invalid_token — see handlers.ts
    )

    await expect(api.get('/api/probe')).rejects.toThrow()
    // If window.location.href = '/login' were still in the code this would fail
    expect(location.href).toBe('http://localhost:3000/')
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
