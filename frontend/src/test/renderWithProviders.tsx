import type { ReactNode } from 'react'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { BalanceContext } from '../context/BalanceContext'

interface WithProvidersProps {
  user: User | null | undefined
  balance?: string | null | undefined
  children: ReactNode
}

/** Stubs AuthContext and BalanceContext for a test, without hitting the
 * network. `balance` defaults to `undefined` (still loading), the value
 * every page test but AppNavbar's needs. */
export function WithProviders({ user, balance = undefined, children }: WithProvidersProps) {
  return (
    <AuthContext.Provider value={{ user, login: () => {}, logout: async () => {} }}>
      <BalanceContext.Provider value={{ balance, refetch: async () => {} }}>
        {children}
      </BalanceContext.Provider>
    </AuthContext.Provider>
  )
}
