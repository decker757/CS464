import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useBalance } from '../context/BalanceContext'
import { formatCredits } from '../utils/formatCredits'
import AccountMenu from './AccountMenu'
import TopBar from './layout/TopBar'
import { buttonClass } from './ui/buttonClass'

// NavLink sets aria-current="page" on the match itself, so "you are here" is
// carried by the markup and not only by the colour (#356). Prefix matching is
// deliberate: /markets/:id is still the Markets section, which is where a
// trader spends most of their time.
function navLinkClass({ isActive }: { isActive: boolean }): string {
  return `text-sm transition ${isActive ? 'font-semibold text-smu-gold' : 'text-white/75 hover:text-white'}`
}

/** The balance as a readout and not a control: a figure and its unit, inside
 * the account cluster. It used to carry the active link's exact two classes,
 * which read as a page you could open (#437) — so the emphasis is on the
 * number alone now, and the gold stays with the navigation it belongs to.
 *
 * The figure is `available`, not `balance`: once a resting buy order reserves
 * credits they are still counted in `balance` but cannot be spent, so a
 * readout labelled "available balance" that showed `balance` would overstate
 * it by exactly what the trader's own orders hold (ADR 0024, #61).
 *
 * It goes through `formatCredits`, which is also what the portfolio's balance
 * card uses: this bar and that card show figures from the same response, and
 * one truncating while the other did not is how the same screen came to read
 * 9,993 here and 9,993.8938 there (#443). Pick a precision for one of them
 * and it has to be the same helper, or they drift again. */
function BalanceReadout({ available }: { available: string | null }) {
  return (
    <span aria-label="available balance" className="text-sm whitespace-nowrap text-white/70">
      {available === null ? (
        'Balance unavailable'
      ) : (
        <>
          <span className="font-semibold text-white tabular-nums">{formatCredits(available)}</span> credits
        </>
      )}
    </span>
  )
}

export default function AppNavbar() {
  const { user, logout } = useAuth()
  const { available } = useBalance()
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

  // The brand half of the bar is public and the rest of it is not, so no page
  // offers "Log Out" to a signed-out visitor.
  if (!user) return <TopBar />

  return (
    <TopBar
      nav={
        <>
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
        </>
      }
    >
      {/* Money, identity and sign-out as one group at the far right, outside
          the navigation: the balance stays on screen because it is the figure
          a trader checks most, and the menu holds the two things that are
          about the account rather than about where to go. */}
      <div className="flex items-center gap-4 rounded-full border border-white/10 bg-white/5 py-1 pr-1 pl-4">
        {available !== undefined && <BalanceReadout available={available} />}
        <AccountMenu username={user.username} email={user.email} isLoggingOut={loggingOut} onLogout={handleLogout} />
      </div>
    </TopBar>
  )
}
