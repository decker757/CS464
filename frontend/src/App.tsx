import { BrowserRouter, Route, Routes } from 'react-router-dom'
import GuestOnly from './components/GuestOnly'
import ProtectedRoute from './components/ProtectedRoute'
import { AuthProvider } from './context/AuthContext'
import { BalanceProvider } from './context/BalanceContext'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import MarketDetailPage from './pages/MarketDetailPage'
import MarketsPage from './pages/MarketsPage'
import RegisterPage from './pages/RegisterPage'
import AdminMarketsPage from './pages/admin/AdminMarketsPage'
import CreateMarketPage from './pages/admin/CreateMarketPage'
import DecideOutcomePage from './pages/admin/DecideOutcomePage'
import ProposalsPage from './pages/admin/ProposalsPage'
import ProposeOutcomePage from './pages/admin/ProposeOutcomePage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <BalanceProvider>
          <Routes>
            <Route path="/" element={<GuestOnly><LandingPage /></GuestOnly>} />
            <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
            <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
            <Route path="/markets" element={<ProtectedRoute><MarketsPage /></ProtectedRoute>} />
            <Route path="/markets/:id" element={<ProtectedRoute><MarketDetailPage /></ProtectedRoute>} />
            <Route path="/admin/markets" element={<ProtectedRoute requireAdmin><AdminMarketsPage /></ProtectedRoute>} />
            <Route path="/admin/markets/new" element={<ProtectedRoute requireAdmin><CreateMarketPage /></ProtectedRoute>} />
            <Route path="/admin/markets/:id/propose-outcome" element={<ProtectedRoute requireAdmin><ProposeOutcomePage /></ProtectedRoute>} />
            <Route path="/admin/proposals" element={<ProtectedRoute requireAdmin><ProposalsPage /></ProtectedRoute>} />
            <Route path="/admin/markets/:id/decide-outcome" element={<ProtectedRoute requireAdmin><DecideOutcomePage /></ProtectedRoute>} />
            {/* /portfolio — added as built */}
          </Routes>
        </BalanceProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
