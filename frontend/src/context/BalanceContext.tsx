import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import * as ledgerApi from '../api/ledgerApi'
import { useAuth } from './AuthContext'

interface BalanceContextType {
  // undefined: not yet loaded. null: load failed. A string: the ledger's own
  // decimal balance, formatted with formatCredits by the caller.
  //
  // `balance` is every credit on the account, including those a resting buy
  // order reserves. `available` is what a new trade or order may actually
  // spend — `balance` less those reservations (ADR 0024). Show `available`
  // wherever the figure means "what you can spend"; `balance` is for net
  // worth and for the history's running total.
  balance: string | null | undefined
  available: string | null | undefined
  // Re-reads the authoritative balance from the ledger — call this after a
  // trade commits ([B-2] #33's third acceptance criterion). A payout has no
  // caller on the trader's tab, so the provider itself re-reads when the
  // trader comes back to the tab (#511). A failed request leaves the last
  // known balance on screen rather than blanking it, so a rejected trade
  // never looks like it changed anything.
  refetch: () => Promise<void>
}

export const BalanceContext = createContext<BalanceContextType | null>(null)

export function BalanceProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  const [balance, setBalance] = useState<string | null | undefined>(undefined)
  const [available, setAvailable] = useState<string | null | undefined>(undefined)
  // Whose balance `balance` belongs to, so a user switching to another on the
  // same browser session — without an intervening null, if `login` is called
  // directly — never shows the previous user's figure while the new fetch is
  // still in flight. React's "adjust state during render" pattern: comparing
  // against a ref here would still paint one frame of the stale value first.
  const [owner, setOwner] = useState<string | null>(null)

  // The most recent refetch's own number — not a boolean flag, so an older
  // request landing after a newer one started (a trade's refetch answering
  // before the initial mount's does, say) is dropped rather than
  // overwriting the newer answer with a stale one.
  const latestRequestRef = useRef(0)

  const refetch = useCallback(() => {
    const requestId = ++latestRequestRef.current
    return ledgerApi.getMyBalance()
      .then(result => {
        if (requestId !== latestRequestRef.current) return
        setBalance(result.balance)
        setAvailable(result.available)
      })
      // Keep whatever was last shown; only drop to null if nothing has loaded yet.
      .catch(() => {
        if (requestId !== latestRequestRef.current) return
        setBalance(current => (current === undefined ? null : current))
        setAvailable(current => (current === undefined ? null : current))
      })
  }, [])

  if ((user?.id ?? null) !== owner) {
    setOwner(user?.id ?? null)
    setBalance(undefined)
    setAvailable(undefined)
  }

  useEffect(() => {
    if (user) refetch()
  }, [user, refetch])

  // Re-read whenever the trader comes back to this tab. A payout, or a fill
  // while the trader is on another page, is written by somebody else's
  // request, and nothing tells this tab about it (#511): the socket has no
  // payout frame, and the market page's order frame refetches only while
  // that page is open. Both events, because clicking from one browser window
  // into another fires `focus` and often no `visibilitychange`, while
  // switching tabs fires `visibilitychange`. When both fire, the two reads
  // race like any others: `latestRequestRef` keeps the newer answer.
  useEffect(() => {
    if (!user) return

    function handleReturn() {
      if (document.visibilityState === 'visible') refetch()
    }

    window.addEventListener('focus', handleReturn)
    document.addEventListener('visibilitychange', handleReturn)
    return () => {
      window.removeEventListener('focus', handleReturn)
      document.removeEventListener('visibilitychange', handleReturn)
    }
  }, [user, refetch])

  return (
    <BalanceContext.Provider value={{ balance, available, refetch }}>
      {children}
    </BalanceContext.Provider>
  )
}

export const useBalance = (): BalanceContextType => {
  const ctx = useContext(BalanceContext)
  if (!ctx) throw new Error('useBalance must be used within BalanceProvider')
  return ctx
}
