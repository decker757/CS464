import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useBalance } from '../context/BalanceContext'
import { formatCredits } from '../utils/formatCredits'
import TopBar from './layout/TopBar'
import Button from './ui/Button'
import { buttonClass } from './ui/buttonClass'

// NavLink sets aria-current="page" on the match itself, so "you are here" is
// carried by the markup and not only by the colour (#356). Prefix matching is
// deliberate: /markets/:id is still the Markets section, which is where a
// trader spends most of their time.
function navLinkClass({ isActive }: { isActive: boolean }): string {
  return `text-sm transition ${isActive ? 'font-semibold text-smu-gold' : 'text-white/75 hover:text-white'}`
}

export default function AppNavbar() {
  const { user, logout } = useAuth()
  const { balance } = useBalance()
  const [loggingOut, setLoggingOut] = useState(false)

  const handleLogout = async () => {
    if (loggingOut) return // a double click would send two POSTs
    setLoggingOut(true)
    try {
      // No navigate() here: clearing `user` lets ProtectedRoute redirect to
      // /login, and it must stay the only thing that decides where to go.
      await logout()
    } catch {
      // `logout` clears the local session either way; there is nothing to retry.
    } finally {
      setLoggingOut(false)
    }
  }

  // Everything is behind `user`, so no page offers "Log Out" to a signed-out visitor.
  return (
    <TopBar>
      {user && (
        <div className="flex items-center gap-4">
          {/* First, because it is the thing the product is for. */}
          <NavLink to="/markets" className={navLinkClass}>
            Markets
          </NavLink>
          <NavLink to="/portfolio" className={navLinkClass}>
            Portfolio
          </NavLink>
          <NavLink to="/history" className={navLinkClass}>
            History
          </NavLink>
          <NavLink to="/leaderboard" className={navLinkClass}>
            Leaderboard
          </NavLink>
          {user.role === 'admin' && (
            <>
              {/* The admin tools are a different kind of thing from the four
                  above — a separate list, other people's markets, other
                  people's accounts — so they are fenced off rather than
                  sitting in the same run of links. */}
              <span aria-hidden="true" className="h-5 w-px bg-white/25" />
              <NavLink to="/admin/markets" end className={navLinkClass}>
                All Markets
              </NavLink>
              <NavLink to="/admin/proposals" className={navLinkClass}>
                Proposals
              </NavLink>
              <NavLink to="/admin/users" className={navLinkClass}>
                Users
              </NavLink>
              <NavLink to="/admin/markets/new" className={buttonClass('outlineGoldOnNavy', 'sm')}>
                + New Market
              </NavLink>
            </>
          )}
          {balance !== undefined && (
            <span aria-label="available balance" className="text-sm font-semibold text-smu-gold">
              {balance === null ? 'Balance unavailable' : `${formatCredits(balance)} credits`}
            </span>
          )}
          <span className="text-sm text-white/75">{user.username}</span>
          <Button variant="outlineOnNavy" size="sm" onClick={handleLogout} disabled={loggingOut}>
            {loggingOut ? 'Signing out…' : 'Log Out'}
          </Button>
        </div>
      )}
    </TopBar>
  )
}
