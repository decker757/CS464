import AppNavbar from '../components/AppNavbar'
import { CREAM, NAV } from '../theme/colors'

export default function MarketsPage() {
  return (
    <div style={{ minHeight: '100vh', backgroundColor: CREAM }}>
      <AppNavbar />
      <main style={{ maxWidth: 1200, margin: '0 auto', padding: '48px 32px' }}>
        <h1 style={{ fontSize: 28, fontWeight: 800, color: NAV }}>Markets</h1>
      </main>
    </div>
  )
}
