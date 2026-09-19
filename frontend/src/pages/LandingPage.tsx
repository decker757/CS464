import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import LandingFeatures from '../components/landing/LandingFeatures'
import LandingFooter from '../components/landing/LandingFooter'
import LandingHero from '../components/landing/LandingHero'
import LandingNavbar from '../components/landing/LandingNavbar'
import { useAuth } from '../context/AuthContext'

export default function LandingPage() {
  const { user } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (user) navigate('/markets', { replace: true })
  }, [user, navigate])

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', backgroundColor: '#FAF8F3' }}>
      <LandingNavbar />
      <main style={{ flex: 1 }}>
        <LandingHero />
        <LandingFeatures />
      </main>
      <LandingFooter />
    </div>
  )
}
