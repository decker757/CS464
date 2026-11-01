import type { ReactNode } from 'react'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { BalanceContext } from '../context/BalanceContext'

interface WithProvidersProps {
  user: User | null | undefined
  balance?: string | null | undefined
  // What a new trade or order may spend — `balance` less what resting buy
  // orders reserve (ADR 0024). Defaults to `balance`, which is the figure
  // for an account with no resting orders.
  available?: string | null | undefined
  // BalanceContext's refetch, for a test that checks when the balance is
  // re-read. A no-op by default.
  refetchBalance?: () => Promise<void>
  children: ReactNode
}

/** Stubs AuthContext and BalanceContext for a test, without hitting the
 * network. `balance` defaults to `undefined` (still loading), the value
 * every page test but AppNavbar's needs. */
export function WithProviders({
  user, balance, available = balance, refetchBalance = async () => {}, children,
}: WithProvidersProps) {
  return (
    <AuthContext.Provider value={{ user, login: () => {}, logout: async () => {} }}>
      <BalanceContext.Provider value={{ balance, available, refetch: refetchBalance }}>
        {children}
      </BalanceContext.Provider>
    </AuthContext.Provider>
  )
}
