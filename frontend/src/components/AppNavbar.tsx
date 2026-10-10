import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useBalance } from '../context/BalanceContext'
import { formatCredits } from '../utils/formatCredits'
import TopBar from './layout/TopBar'
import Button from './ui/Button'
import { buttonClass } from './ui/buttonClass'

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
          <Link to="/portfolio" className="text-sm text-white/75 hover:text-white">
            Portfolio
          </Link>
          <Link to="/history" className="text-sm text-white/75 hover:text-white">
            History
          </Link>
          {user.role === 'admin' && (
            <>
              <Link to="/admin/markets" className="text-sm text-white/75 hover:text-white">
                All Markets
              </Link>
              <Link to="/admin/proposals" className="text-sm text-white/75 hover:text-white">
                Proposals
              </Link>
              <Link to="/admin/markets/new" className={buttonClass('outlineGoldOnNavy', 'sm')}>
                + New Market
              </Link>
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
