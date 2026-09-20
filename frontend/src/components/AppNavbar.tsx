import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { GOLD, NAV } from '../theme/colors'
import { TrendIcon } from './auth/AuthIcons'

export default function AppNavbar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/')
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

        <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
          {user && (
            <span style={{ color: 'rgba(255,255,255,0.75)', fontSize: 14 }}>
              {user.username}
            </span>
          )}
          <button
            onClick={handleLogout}
            style={{
              color: '#fff',
              fontSize: 14,
              fontWeight: 500,
              padding: '8px 18px',
              borderRadius: 8,
              border: '1px solid rgba(255,255,255,0.35)',
              background: 'transparent',
              cursor: 'pointer',
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
            Log Out
          </button>
        </div>
      </nav>
    </header>
  )
}
