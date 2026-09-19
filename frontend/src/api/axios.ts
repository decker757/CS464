import axios, { InternalAxiosRequestConfig } from 'axios'
import { ApiError } from './errors'

interface RetryConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
}

interface QueueEntry {
  resolve: () => void
  reject: (error: unknown) => void
}

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000',
  withCredentials: true,
})

let isRefreshing = false
let queue: QueueEntry[] = []

const processQueue = (error: unknown) => {
  queue.forEach((p) => (error ? p.reject(error) : p.resolve()))
  queue = []
}

api.interceptors.response.use(
  (res) => res,
  async (error: unknown) => {
    if (!axios.isAxiosError<ApiError>(error)) return Promise.reject(error)

    const original = error.config as RetryConfig | undefined
    const code = error.response?.data?.error?.code

    // Never retry the refresh request itself — that would loop forever
    const isRefreshRequest = original?.url?.endsWith('/auth/refresh')

    if (code === 'invalid_token' && original && !original._retry && !isRefreshRequest) {
      if (isRefreshing) {
        return new Promise<void>((resolve, reject) => {
          queue.push({ resolve, reject })
        }).then(() => api(original))
      }

      original._retry = true
      isRefreshing = true

      try {
        // navigator.locks serialises refresh across tabs so two tabs never
        // send the same one-time refresh token simultaneously
        await navigator.locks.request('auth-refresh', () => api.post('/auth/refresh'))
        processQueue(null)
        return api(original)
      } catch (refreshError) {
        processQueue(refreshError)
        // No redirect here — ProtectedRoute sends unauthenticated users to /login
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    return Promise.reject(error)
  }
)

export default api
