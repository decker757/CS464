import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './server'

// navigator.locks is not implemented in jsdom — provide a passthrough mock so
// every test file sees the real interceptor behaviour rather than a crash
Object.defineProperty(navigator, 'locks', {
  value: {
    request: (_name: string, callback: () => Promise<unknown>) => callback(),
  },
  configurable: true,
  writable: true,
})

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
