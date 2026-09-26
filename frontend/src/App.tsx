import { BrowserRouter, Route, Routes } from 'react-router-dom'
import GuestOnly from './components/GuestOnly'
import ProtectedRoute from './components/ProtectedRoute'
import { AuthProvider } from './context/AuthContext'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import MarketDetailPage from './pages/MarketDetailPage'
import MarketsPage from './pages/MarketsPage'
import RegisterPage from './pages/RegisterPage'
import CreateMarketPage from './pages/admin/CreateMarketPage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<GuestOnly><LandingPage /></GuestOnly>} />
          <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
          <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
          <Route path="/markets" element={<ProtectedRoute><MarketsPage /></ProtectedRoute>} />
          <Route path="/markets/:id" element={<ProtectedRoute><MarketDetailPage /></ProtectedRoute>} />
          <Route path="/admin/markets/new" element={<ProtectedRoute requireAdmin><CreateMarketPage /></ProtectedRoute>} />
          {/* /portfolio — added as built */}
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
