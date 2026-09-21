import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { GOLD, NAV } from '../theme/colors'
import { TrendIcon } from './auth/AuthIcons'

export default function AppNavbar() {
  const { user, logout } = useAuth()
  const [loggingOut, setLoggingOut] = useState(false)

  const handleLogout = async () => {
    // Guarded like every other button here that sends a request: an impatient
    // double click would otherwise fire two POSTs.
    if (loggingOut) return
    setLoggingOut(true)
    try {
      await logout()
      // No navigate() here. `logout` clears `user` in this same render pass,
      // which unmounts this navbar (it lives under ProtectedRoute) in favour
      // of ProtectedRoute's own <Navigate to="/login">. That guard is the
      // only thing deciding where a signed-out visitor goes; a second,
      // imperative redirect from here raced it and reliably lost anyway.
    } catch {
      // `logout` clears the local session in a `finally`, so by this point the
      // user is signed out whatever the network did. Swallowed rather than
      // surfaced: there is nothing here for them to retry, and an unhandled
      // rejection is the one outcome that leaves the button looking broken.
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <header style={{ backgroundColor: NAV }}>
      <nav
        style={{
          maxWidth: 1200,
          margin: '0 auto',
          padding: '0 32px',
          height: 72,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#fff', fontSize: 20, fontWeight: 700, letterSpacing: '-0.3px' }}>
          <TrendIcon size={20} color={GOLD} />
          PredictSMU
        </span>

        {/* Both the name and the control are behind `user`: a navbar that
            offers "Log Out" to somebody it knows is signed out is a bug
            waiting for the first page that is not behind ProtectedRoute. */}
        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          {user && (
            <>
              <span style={{ color: 'rgba(255,255,255,0.75)', fontSize: 14 }}>
                {user.username}
              </span>
              <button
                type="button"
                onClick={handleLogout}
                disabled={loggingOut}
                style={{
                  color: '#fff',
                  fontSize: 14,
                  fontWeight: 500,
                  padding: '8px 18px',
                  borderRadius: 8,
                  border: '1px solid rgba(255,255,255,0.35)',
                  background: 'transparent',
                  cursor: loggingOut ? 'default' : 'pointer',
                  opacity: loggingOut ? 0.6 : 1,
                  transition: 'background 0.15s, border-color 0.15s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'rgba(255,255,255,0.1)'
                  e.currentTarget.style.borderColor = 'rgba(255,255,255,0.6)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                  e.currentTarget.style.borderColor = 'rgba(255,255,255,0.35)'
                }}
              >
                {loggingOut ? 'Signing out…' : 'Log Out'}
              </button>
            </>
          )}
        </div>
      </nav>
    </header>
  )
}
