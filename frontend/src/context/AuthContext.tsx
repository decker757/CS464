import { createContext, useContext, useEffect, useState } from 'react'
import api from '../api/axios'

export interface User {
  id: string
  username: string
  email: string
  role: 'trader' | 'admin'
  created_at: string
}

interface AuthContextType {
  user: User | null | undefined
  login: (user: User) => void
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined)

  useEffect(() => {
    // This request answers one question — "is anyone already signed in?" — and
    // it is only ever allowed to resolve the *unknown* state. If something
    // else has decided who is logged in while it was in flight, that answer is
    // newer than this one and has to win, whichever way this one lands.
    //
    // The window is not narrow. An unauthenticated /auth/me comes back
    // `invalid_token`, which sends the axios interceptor through
    // navigator.locks and an entire /auth/refresh before this promise settles
    // — and /login and /register render the whole time, because App.tsx puts
    // them outside ProtectedRoute. Logging in during that gap is ordinary use,
    // not a stress test, and the late `setUser(null)` used to throw the login
    // away with nothing on screen to explain it.
    const resolveIfStillUnknown = (value: User | null) =>
      setUser((current) => (current === undefined ? value : current))

    api.get<User>('/auth/me')
      .then((res) => resolveIfStillUnknown(res.data))
      .catch(() => resolveIfStillUnknown(null))
  }, [])

  const login = (userData: User) => setUser(userData)
  const logout = async () => {
    await api.post('/auth/logout')
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = (): AuthContextType => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
