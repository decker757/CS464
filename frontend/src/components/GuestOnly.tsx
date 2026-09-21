import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

interface GuestOnlyProps {
  children: React.ReactNode
}

/** The mirror of ProtectedRoute: for pages only a signed-out visitor needs.
 *
 * It is also the single redirect authority for a *successful sign-in*, which is
 * why neither LoginPage nor RegisterPage calls `navigate` any more. `login()`
 * sets `user` while the browser is still on /login, so this guard re-renders
 * and redirects before an imperative call from the page could take effect —
 * two authorities meant the guard silently overwrote the page. Same argument
 * ProtectedRoute settled for the logout redirect: one guard decides, and
 * nothing races it.
 */
export default function GuestOnly({ children }: GuestOnlyProps) {
  const { user } = useAuth()
  const location = useLocation()
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/markets'

  // `from`, not a hardcoded '/markets'. This is the redirect that decides where
  // a new session lands, so hardcoding it here deletes the
  // `state={{ from: location }}` chain ProtectedRoute sets — and nothing
  // notices while /markets is the only protected route, because then `from`
  // *is* /markets. That is how this broke twice.
  if (user) return <Navigate to={from} replace />

  // No loading branch, deliberately — `undefined` renders the page too.
  // The session is in an httpOnly cookie, so "not known yet" costs an
  // unauthenticated /auth/me plus the interceptor's whole /auth/refresh before
  // it resolves to null. None of these three pages needs the answer in order
  // to render, and a guest is the common visitor to all of them, so gating on
  // it blocks the public landing page behind two failed requests. A signed-in
  // visitor is bounced one render later instead, which is the cheaper miss.
  return <>{children}</>
}
