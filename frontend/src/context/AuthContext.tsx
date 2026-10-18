import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { onSessionExpired } from '../api/axios'
import * as authApi from '../api/authApi'
import type { User } from '../api/authApi'

export type { User }

interface AuthContextType {
  user: User | null | undefined
  /** True once a session somebody was signed into ended on its own, so
   *  /login can say why they are looking at it. Left out means no. */
  hasSessionExpired?: boolean
  login: (user: User) => void
  logout: () => Promise<void>
}

/** Both facts in one state value, because the second is only ever decided
 *  from the first — see the listener below. */
interface Session {
  user: User | null | undefined
  hasSessionExpired: boolean
}

export const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session>({ user: undefined, hasSessionExpired: false })

  // The lookup below fails down the very path that reports an expired session
  // — 401, then a refused /auth/refresh — and that failure means "nobody was
  // signed in", never "your session just ended". It has to be ignored even
  // after somebody signs in during the window, which is the same late answer
  // the lookup itself is guarded against below.
  const isLookingUpTheSessionRef = useRef(true)

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
      setSession((current) =>
        current.user === undefined ? { ...current, user: value } : current,
      )

    authApi.getCurrentUser()
      .then(resolveIfStillUnknown)
      .catch(() => resolveIfStillUnknown(null))
      .finally(() => { isLookingUpTheSessionRef.current = false })
  }, [])

  useEffect(
    () =>
      // The interceptor's refresh was refused, so the session is over (#353).
      // Clearing the user here is what makes ProtectedRoute redirect; without
      // it every page fails while the navbar still shows a name and a balance.
      onSessionExpired(() => {
        if (isLookingUpTheSessionRef.current) return
        setSession((current) =>
          // Only a session somebody is in can expire. A visitor who is not
          // signed in has nothing to lose, and telling them otherwise would
          // be a lie.
          current.user ? { user: null, hasSessionExpired: true } : current,
        )
      }),
    [],
  )

  const login = (userData: User) => setSession({ user: userData, hasSessionExpired: false })
  const logout = async () => {
    // `finally`, because logging out is the one action a user has to be able
    // to trust. The server's own contract says this endpoint is
    // unauthenticated and always succeeds (docs/api/auth-service.md), so a
    // rejection here is a network fault rather than a refusal — and leaving
    // the session up because the network blinked is the wrong way to fail.
    // The error still propagates; the caller decides what to show.
    try {
      await authApi.logout()
    } finally {
      setSession({ user: null, hasSessionExpired: false })
    }
  }

  return (
    <AuthContext.Provider value={{ ...session, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = (): AuthContextType => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
