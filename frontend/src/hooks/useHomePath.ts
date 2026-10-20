import { useAuth } from '../context/AuthContext'

/**
 * The page this visitor calls home: the market list once signed in, the
 * landing page otherwise. The wordmark in TopBar and the 404 page's way back
 * both point here, so the two cannot disagree about where home is.
 *
 * `undefined` is "not known yet", and the pages rendered in that state are the
 * guest ones (GuestOnly renders while it resolves), so it counts as a guest.
 */
export function useHomePath(): string {
  const { user } = useAuth()
  return user ? '/markets' : '/'
}
