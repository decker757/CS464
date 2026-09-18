import { createContext, useContext, useEffect, useState } from 'react'
import api from '../api/axios'

export interface User {
  id: string
  username: string
  email: string
  role: 'trader' | 'admin'
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
    api.get<User>('/auth/me')
      .then((res) => setUser(res.data))
      .catch(() => setUser(null))
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
