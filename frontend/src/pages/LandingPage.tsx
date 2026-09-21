import LandingFeatures from '../components/landing/LandingFeatures'
import LandingFooter from '../components/landing/LandingFooter'
import LandingHero from '../components/landing/LandingHero'
import LandingNavbar from '../components/landing/LandingNavbar'
import { CREAM } from '../theme/colors'

export default function LandingPage() {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', backgroundColor: CREAM }}>
      <LandingNavbar />
      <main style={{ flex: 1 }}>
        <LandingHero />
        <LandingFeatures />
      </main>
      <LandingFooter />
    </div>
  )
}
