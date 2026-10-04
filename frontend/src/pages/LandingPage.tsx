import LandingFeatures from '../components/landing/LandingFeatures'
import LandingFooter from '../components/landing/LandingFooter'
import LandingHero from '../components/landing/LandingHero'
import LandingNavbar from '../components/landing/LandingNavbar'

export default function LandingPage() {
  return (
    <div className="flex min-h-screen flex-col bg-smu-cream">
      <LandingNavbar />
      <main className="flex-1">
        <LandingHero />
        <LandingFeatures />
      </main>
      <LandingFooter />
    </div>
  )
}
