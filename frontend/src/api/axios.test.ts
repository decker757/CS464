import { afterEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import api, { onSessionExpired } from './axios'

// navigator.locks mock lives in src/test/setup.ts and applies to every file
const passthroughLocks = navigator.locks

const invalidToken = (status = 401) =>
  HttpResponse.json(
    { error: { code: 'invalid_token', message: 'Not authenticated.' } },
    { status },
  )

describe('axios refresh interceptor', () => {
  let stopListening: (() => void) | null = null

  afterEach(() => {
    vi.unstubAllGlobals()
    stopListening?.()
    stopListening = null
    Object.defineProperty(navigator, 'locks', {
      value: passthroughLocks,
      configurable: true,
      writable: true,
    })
  })

  /** Listens the way AuthProvider does, and stops after the test. */
  const listenForSessionEnd = () => {
    const sessionEnded = vi.fn()
    stopListening = onSessionExpired(sessionEnded)
    return sessionEnded
  }

  it('retries the original request after a successful /auth/refresh, and leaves the session alone', async () => {
    const sessionEnded = listenForSessionEnd()
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
    // One 401 the refresh resolved is not the end of a session (#353).
    expect(sessionEnded).not.toHaveBeenCalled()
  })

  it('reports the session as ended when /auth/refresh fails, and redirects nobody', async () => {
    const sessionEnded = listenForSessionEnd()
    // Only href is read back: a redirect from here would change it.
    const location = { href: 'http://localhost:3000/' }
    vi.stubGlobal('location', location)

    server.use(
      http.get('http://localhost:8000/api/probe', () => invalidToken()),
      // default /auth/refresh handler returns invalid_token — see handlers.ts
    )

    await expect(api.get('/api/probe')).rejects.toThrow()
    // Nothing else can tell AuthContext the session is over, and without it
    // ProtectedRoute's redirect to /login never fires (#353).
    expect(sessionEnded).toHaveBeenCalledTimes(1)
    // Reporting it is the whole job: where that lands is ProtectedRoute's to
    // decide. A `window.location.href = '/login'` here would be a second
    // authority racing the guard, and would fail this line.
    expect(location.href).toBe('http://localhost:3000/')
  })

  it('sends the refresh inside the cross-tab auth-refresh lock', async () => {
    const lockNames: string[] = []
    Object.defineProperty(navigator, 'locks', {
      value: {
        request: (name: string, callback: () => Promise<unknown>) => {
          lockNames.push(name)
          return callback()
        },
      },
      configurable: true,
      writable: true,
    })
    server.use(http.get('http://localhost:8000/api/probe', () => invalidToken()))

    await expect(api.get('/api/probe')).rejects.toThrow()

    // Two tabs holding the same one-time refresh token must not spend it twice.
    expect(lockNames).toEqual(['auth-refresh'])
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
